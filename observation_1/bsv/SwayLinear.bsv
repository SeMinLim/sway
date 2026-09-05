package SwayLinear;
import Vector::*;
import FIFO::*;
import FIFOF::*;
import BRAM::*;
import GetPut::*;
import SwayTypes::*;

typedef struct {
	SwayValue x;
	Bit#(9) row;
	Bool firstCol;
	Bool lastCol;
} SwayLinearMeta deriving (Bits, Eq);

typedef struct {
	SwayLinearMeta meta;
	Vector#(4, Int#(32)) products;
} SwayProducts deriving (Bits, Eq);

module mkSwayLinear#(String weightFile, Integer shiftBits)(SwayVectorIfc#(ni, no));
	Integer nIn = valueOf(ni);
	Integer nOut = valueOf(no);
	Integer groups = (nOut + 3) / 4;
	FIFOF#(SwayFrame#(ni)) inputQ <- mkSizedFIFOF(1);
	Reg#(Bool) outputReadyOn <- mkReg(False);
	Reg#(Bool) outputFirstR <- mkReg(False);
	Reg#(Bit#(16)) outputTagR <- mkReg(0);
	FIFO#(SwayLinearMeta) requestQ <- mkSizedFIFO(4);
	FIFO#(SwayProducts) productQ <- mkSizedFIFO(4);
	BRAM_Configure cfg = defaultValue;
	cfg.memorySize = groups * (nIn + 1);
	cfg.latency = 1;
	cfg.loadFormat = tagged Hex weightFile;
	BRAM1Port#(Bit#(15), Bit#(64)) weights <- mkBRAM1Server(cfg);

	let inputR = inputQ.first;
	// Static write enables avoid nested dynamic updates to one packed register.
	// Capacity and the token holding lifetime are unchanged.
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

	// [STAGE 1] Retain one token; independent engines overlap different tokens.
	rule process1 ( !computeOn && !outputReadyOn && inputQ.notEmpty );
		columnCnt <= 0;
		rowCnt <= 0;
		addressCnt <= 0;
		issueDone <= False;
		computeOn <= True;
	endrule

	// Packed SRAM supplies four output-row weights per cycle.
	// The final column multiplies each bias by the fixed alignment scale.
	rule process2 ( computeOn && !issueDone );
		SwayValue x = fromInteger(2 ** shiftBits);
		if ( columnCnt < fromInteger(nIn) ) x = inputR.data[columnCnt];
		weights.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: addressCnt, datain: 0});
		requestQ.enq(SwayLinearMeta {x: x, row: rowCnt,
			firstCol: columnCnt == 0, lastCol: columnCnt == fromInteger(nIn)});
		addressCnt <= addressCnt + 1;
		if ( columnCnt == fromInteger(nIn) ) begin
			columnCnt <= 0;
			if ( rowCnt + 4 >= fromInteger(nOut) ) issueDone <= True;
			else rowCnt <= rowCnt + 4;
		end else columnCnt <= columnCnt + 1;
	endrule

	// [STAGE 2] Registered four-lane multiply with aligned row metadata.
	rule process3;
		let packedWeight <- weights.portA.response.get;
		let meta = requestQ.first;
		requestQ.deq;
		Vector#(4, SwayValue) w = unpack(packedWeight);
		Vector#(4, Int#(32)) p = newVector;
		for ( Integer i = 0; i < 4; i = i + 1 ) begin
			p[i] = swayProduct(meta.x, w[i]);
		end
		productQ.enq(SwayProducts {meta: meta, products: p});
	endrule

	// [STAGE 3] Wide accumulation, then one rounded output per row.
	rule process4 ( computeOn );
		let item = productQ.first;
		productQ.deq;
		Vector#(4, SwayValue) rowValues = newVector;
		for ( Integer i = 0; i < 4; i = i + 1 ) begin
			SwayAcc sum = signExtend(item.products[i]);
			if ( !item.meta.firstCol ) sum = sum + accR[i];
			accR[i] <= sum;
			rowValues[i] = swayRound(sum, shiftBits);
		end
		for ( Integer row = 0; row < nOut; row = row + 1 ) begin
			if ( item.meta.lastCol && item.meta.row == fromInteger((row / 4) * 4) ) begin
				outputBuffer[row] <= rowValues[row % 4];
			end
		end
		mulCnt <= mulCnt + 4;
		if ( item.meta.lastCol && item.meta.row + 4 >= fromInteger(nOut) ) begin
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
