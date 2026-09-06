package SwayLinear;
import Vector::*;
import FIFO::*;
import FIFOF::*;
import BRAM::*;
import GetPut::*;
import Assert::*;

import SwayTypes::*;

typedef struct {
	SwayValue x;
	Bit#(9) row;
	Bool firstCol;
	Bool lastCol;
} SwayLinearMeta deriving (Bits, Eq);

typedef struct {
	Bit#(9) column;
	Bit#(9) row;
	Bit#(15) address;
	Bool firstCol;
	Bool lastCol;
} SwayLinearIssue deriving (Bits, Eq);

typedef struct {
	SwayLinearIssue issue;
	Vector#(16, SwayValue) candidates;
} SwayLinearSelect8 deriving (Bits, Eq);

typedef struct {
	SwayLinearIssue issue;
	Vector#(4, SwayValue) candidates;
} SwayLinearSelect4 deriving (Bits, Eq);

typedef struct {
	SwayLinearMeta meta;
	Bit#(15) address;
} SwayLinearSelected deriving (Bits, Eq);

typedef struct {
	SwayLinearMeta meta;
	Vector#(4, SwayValue) weights;
} SwayLinearOperands deriving (Bits, Eq);

typedef struct {
	SwayLinearMeta meta;
	Vector#(4, Int#(32)) products;
} SwayProducts deriving (Bits, Eq);

typedef struct {
	Bit#(9) row;
	Bool lastRow;
	Vector#(4, SwayAcc) sums;
} SwayLinearSums deriving (Bits, Eq);

typedef struct {
	Bit#(9) row;
	Bool lastRow;
	Vector#(4, SwayValue) values;
} SwayLinearRows deriving (Bits, Eq);

module mkSwayLinear#(String weightFile, Integer shiftBits)(SwayVectorIfc#(ni, no));
	Integer nIn = valueOf(ni);
	Integer nOut = valueOf(no);
	Integer groups = (nOut + 3) / 4;
	staticAssert(nIn > 0 && nIn <= 128,
		"Sway linear selector supports 1..128 input columns");
	staticAssert(nOut > 0 && nOut <= 256 && nOut % 4 == 0,
		"Sway linear profile requires 4..256 output rows in groups of four");
	staticAssert(shiftBits >= 0 && shiftBits < 15,
		"Sway linear bias scale must fit positive signed16");

	FIFOF#(SwayFrame#(ni)) inputQ <- mkSizedFIFOF(1);
	Reg#(Bool) outputReadyOn <- mkReg(False);
	Reg#(Bool) outputFirstR <- mkReg(False);
	Reg#(Bit#(16)) outputTagR <- mkReg(0);
	// Non-bypass two-entry FIFOs cut combinational paths while allowing
	// concurrent producer/consumer operation. Read metadata has four slots.
	FIFO#(SwayLinearSelect8) select8Q <- mkFIFO;
	FIFO#(SwayLinearSelect4) select4Q <- mkFIFO;
	FIFO#(SwayLinearSelected) selectedQ <- mkFIFO;
	FIFO#(SwayLinearMeta) requestQ <- mkSizedFIFO(4);
	FIFO#(SwayLinearOperands) operandsQ <- mkFIFO;
	FIFO#(SwayProducts) productQ <- mkFIFO;
	FIFO#(SwayLinearSums) sumsQ <- mkFIFO;
	FIFO#(SwayLinearRows) rowsQ <- mkFIFO;
	BRAM_Configure cfg = defaultValue;
	cfg.memorySize = groups * (nIn + 1);
	// Register the SRAM output as well as capturing the response below.
	cfg.latency = 2;
	cfg.loadFormat = tagged Hex weightFile;
	BRAM1Port#(Bit#(15), Bit#(64)) weights <- mkBRAM1Server(cfg);

	let inputR = inputQ.first;
	Vector#(no, Reg#(SwayValue)) outputBuffer <- replicateM(mkRegU);
	Vector#(4, Reg#(SwayAcc)) accR <- replicateM(mkReg(0));
	Reg#(Bool) computeOn <- mkReg(False);
	Reg#(Bool) issueDone <- mkReg(False);
	Reg#(Bit#(9)) columnCnt <- mkReg(0);
	Reg#(Bit#(9)) rowCnt <- mkReg(0);
	Reg#(Bit#(15)) addressCnt <- mkReg(0);
	Reg#(Bit#(64)) cycleCnt <- mkReg(0);
	Reg#(Bit#(64)) busyCnt <- mkReg(0);
	Reg#(Bit#(64)) emptyCnt <- mkReg(0);
	Reg#(Bit#(64)) blockedCnt <- mkReg(0);
	Reg#(Bit#(64)) mulCnt <- mkReg(0);

	rule profile;
		cycleCnt <= cycleCnt + 1;
		if ( computeOn ) busyCnt <= busyCnt + 1;
		if ( !computeOn && !inputQ.notEmpty ) emptyCnt <= emptyCnt + 1;
		if ( outputReadyOn ) blockedCnt <= blockedCnt + 1;
	endrule

	// [STAGE 1] Retain the frame until the final row is physically stored.
	rule process1 ( !computeOn && !outputReadyOn && inputQ.notEmpty );
		columnCnt <= 0;
		rowCnt <= 0;
		addressCnt <= 0;
		issueDone <= False;
		computeOn <= True;
	endrule

	// [STAGE 2] First 8:1 selection. Padding is elaboration-time constant;
	// the four-input delta projection does not instantiate a 128-way mux.
	rule process2 ( computeOn && !issueDone );
		Bit#(3) lowIndex = columnCnt[2:0];
		Vector#(16, SwayValue) candidates = newVector;
		for ( Integer groupIdx = 0; groupIdx < 16; groupIdx = groupIdx + 1 ) begin
			Vector#(8, SwayValue) bank = replicate(0);
			for ( Integer lane = 0; lane < 8; lane = lane + 1 ) begin
				if ( groupIdx * 8 + lane < nIn ) begin
					bank[lane] = inputR.data[groupIdx * 8 + lane];
				end
			end
			candidates[groupIdx] = bank[lowIndex];
		end
		SwayLinearIssue issue = SwayLinearIssue {column: columnCnt, row: rowCnt,
			address: addressCnt, firstCol: columnCnt == 0,
			lastCol: columnCnt == fromInteger(nIn)};
		select8Q.enq(SwayLinearSelect8 {issue: issue, candidates: candidates});
		addressCnt <= addressCnt + 1;
		if ( columnCnt == fromInteger(nIn) ) begin
			columnCnt <= 0;
			if ( rowCnt + 4 >= fromInteger(nOut) ) issueDone <= True;
			else rowCnt <= rowCnt + 4;
		end else columnCnt <= columnCnt + 1;
	endrule

	// [STAGE 3] Sixteen registered candidates become four.
	rule process3;
		let item = select8Q.first;
		select8Q.deq;
		Bit#(2) middleIndex = item.issue.column[4:3];
		Vector#(4, SwayValue) candidates = newVector;
		for ( Integer groupIdx = 0; groupIdx < 4; groupIdx = groupIdx + 1 ) begin
			Vector#(4, SwayValue) bank = newVector;
			for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
				bank[lane] = item.candidates[groupIdx * 4 + lane];
			end
			candidates[groupIdx] = bank[middleIndex];
		end
		select4Q.enq(SwayLinearSelect4 {issue: item.issue, candidates: candidates});
	endrule

	// [STAGE 4] Final 4:1 selection, before bias selection or SRAM requests.
	rule process4;
		let item = select4Q.first;
		select4Q.deq;
		Bit#(2) highIndex = item.issue.column[6:5];
		SwayLinearMeta meta = SwayLinearMeta {x: item.candidates[highIndex],
			row: item.issue.row, firstCol: item.issue.firstCol, lastCol: item.issue.lastCol};
		selectedQ.enq(SwayLinearSelected {meta: meta, address: item.issue.address});
	endrule

	// [STAGE 5] Bias is an additional ordered column, with the old scale.
	rule process5;
		let item = selectedQ.first;
		selectedQ.deq;
		SwayLinearMeta meta = item.meta;
		if ( meta.lastCol ) meta.x = fromInteger(2 ** shiftBits);
		weights.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: item.address, datain: 0});
		requestQ.enq(meta);
	endrule

	// [STAGE 6] Capture SRAM response and metadata without multiplication.
	rule process6;
		let packedWeight <- weights.portA.response.get;
		let meta = requestQ.first;
		requestQ.deq;
		operandsQ.enq(SwayLinearOperands {meta: meta, weights: unpack(packedWeight)});
	endrule

	// [STAGE 7] Four full-width products from registered operands only.
	rule process7;
		let item = operandsQ.first;
		operandsQ.deq;
		Vector#(4, Int#(32)) products = newVector;
		for ( Integer i = 0; i < 4; i = i + 1 ) begin
			products[i] = swayProduct(item.meta.x, item.weights[i]);
		end
		productQ.enq(SwayProducts {meta: item.meta, products: products});
	endrule

	// [STAGE 8] Preserve 48-bit ordered accumulation. Only completed rows
	// leave this stage; no saturation or output-register write is in it.
	rule process8 ( computeOn );
		let item = productQ.first;
		productQ.deq;
		Vector#(4, SwayAcc) sums = newVector;
		for ( Integer i = 0; i < 4; i = i + 1 ) begin
			SwayAcc sum = signExtend(item.products[i]);
			if ( !item.meta.firstCol ) sum = sum + accR[i];
			accR[i] <= sum;
			sums[i] = sum;
		end
		mulCnt <= mulCnt + 4;
		if ( item.meta.lastCol ) begin
			sumsQ.enq(SwayLinearSums {row: item.meta.row,
				lastRow: item.meta.row + 4 >= fromInteger(nOut), sums: sums});
		end
	endrule

	// [STAGE 9] Existing arithmetic shift and saturation, after accumulation.
	rule process9;
		let item = sumsQ.first;
		sumsQ.deq;
		Vector#(4, SwayValue) values = newVector;
		for ( Integer i = 0; i < 4; i = i + 1 ) begin
			values[i] = swayRound(item.sums[i], shiftBits);
		end
		rowsQ.enq(SwayLinearRows {row: item.row, lastRow: item.lastRow, values: values});
	endrule

	// [STAGE 10] Static row write enables, driven by registered results.
	rule process10 ( computeOn );
		let item = rowsQ.first;
		rowsQ.deq;
		for ( Integer row = 0; row < nOut; row = row + 1 ) begin
			if ( item.row == fromInteger((row / 4) * 4) ) begin
				outputBuffer[row] <= item.values[row % 4];
			end
		end
		if ( item.lastRow ) begin
			inputQ.deq;
			outputFirstR <= inputR.first;
			outputTagR <= inputR.tag;
			outputReadyOn <= True;
			computeOn <= False;
		end
	endrule

	method Action put(SwayFrame#(ni) data);
		inputQ.enq(data);
	endmethod
	method ActionValue#(SwayFrame#(no)) get if ( outputReadyOn );
		outputReadyOn <= False;
		return SwayFrame {first: outputFirstR, tag: outputTagR, data: readVReg(outputBuffer)};
	endmethod
	method Bool busy = computeOn;
	method SwayStats stats = SwayStats {cycles: cycleCnt, busyCycles: busyCnt,
		mulCount: mulCnt, inputEmptyCycles: emptyCnt, outputFullCycles: blockedCnt};
endmodule

(* synthesize *)
module mkSwayInputProjection(SwayVectorIfc#(64, 256));
	let engine <- mkSwayLinear("data/in_proj.hex", 13);
	return engine;
endmodule

(* synthesize *)
module mkSwayParameterProjection(SwayVectorIfc#(128, 36));
	let engine <- mkSwayLinear("data/x_proj.hex", 13);
	return engine;
endmodule

(* synthesize *)
module mkSwayDeltaProjection(SwayVectorIfc#(4, 128));
	let engine <- mkSwayLinear("data/dt_proj.hex", 13);
	return engine;
endmodule

(* synthesize *)
module mkSwayOutputProjection(SwayVectorIfc#(128, 64));
	let engine <- mkSwayLinear("data/out_proj.hex", 11);
	return engine;
endmodule
endpackage
