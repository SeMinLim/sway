package SwayConv;
import Vector::*;
import FIFO::*;
import FIFOF::*;
import BRAM::*;
import GetPut::*;
import SwayTypes::*;
import SwayNonlinear::*;

// Three registered selection levels: 8:1, 4:1, then 4:1 per x/z channel.
typedef struct {
	Bit#(7) channel;
	Bool first;
	Vector#(16, SwayValue) x;
	Vector#(16, SwayValue) gate;
} SwayConvSelect8 deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	Bool first;
	Vector#(4, SwayValue) x;
	Vector#(4, SwayValue) gate;
} SwayConvSelect4 deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	Bool first;
	SwayValue x;
	SwayValue gate;
} SwayConvInput deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	SwayValue gate;
	Vector#(4, SwayValue) samples;
	Vector#(4, SwayValue) weights;
	SwayAcc bias;
} SwayConvOperands deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	SwayValue gate;
	Vector#(4, Int#(32)) products;
	SwayAcc bias;
} SwayConvProducts deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	SwayValue gate;
	Vector#(2, SwayAcc) sums;
	SwayAcc bias;
} SwayConvPairs deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	SwayValue gate;
	SwayAcc sum;
	SwayAcc bias;
} SwayConvTapSum deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	SwayValue gate;
	SwayAcc sum;
} SwayConvSum deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	SwayValue x;
	SwayValue gate;
} SwayConvActivation deriving (Bits, Eq);

interface SwayConvIfc;
	method Action put(SwayFrame#(256) x);
	method ActionValue#(SwayConvFrame) get;
	method SwayStats stats;
	method Bool busy;
endinterface

(* synthesize *)
module mkSwayConv(SwayConvIfc);
	FIFOF#(SwayFrame#(256)) inputQ <- mkSizedFIFOF(1);
	Reg#(Bool) outputReadyOn <- mkReg(False);
	Reg#(Bool) outputFirstR <- mkReg(False);
	Reg#(Bit#(16)) outputTagR <- mkReg(0);
	// mkFIFO provides a registered two-entry boundary, not a bypass path.
	// Every stage can accept one channel per cycle when its consumer is ready.
	FIFO#(SwayConvSelect8) select8Q <- mkFIFO;
	FIFO#(SwayConvSelect4) select4Q <- mkFIFO;
	FIFO#(SwayConvInput) readQ <- mkSizedFIFO(4);
	FIFO#(SwayConvOperands) operandQ <- mkFIFO;
	FIFO#(SwayConvProducts) productQ <- mkFIFO;
	FIFO#(SwayConvPairs) pairQ <- mkFIFO;
	FIFO#(SwayConvTapSum) tapSumQ <- mkFIFO;
	FIFO#(SwayConvSum) sumQ <- mkFIFO;
	FIFO#(SwayConvActivation) activationQ <- mkFIFO;
	FIFO#(Bit#(7)) resultQ <- mkSizedFIFO(4);
	SwayLutIfc activation <- mkSwayLut("data/silu.hex");
	SwayLutIfc gateActivation <- mkSwayLut("data/gate_silu.hex");
	BRAM_Configure cfg = defaultValue;
	cfg.memorySize = 128;
	cfg.latency = 1;
	cfg.loadFormat = tagged Hex "data/conv.hex";
	BRAM1Port#(Bit#(7), Bit#(80)) weights <- mkBRAM1Server(cfg);
	BRAM_Configure histCfg = defaultValue;
	histCfg.memorySize = 128;
	histCfg.latency = 1;
	BRAM2Port#(Bit#(7), Bit#(48)) history <- mkBRAM2Server(histCfg);
	let inputR = inputQ.first;
	Vector#(128, Reg#(SwayValue)) xBuffer <- replicateM(mkRegU);
	Vector#(128, Reg#(SwayValue)) gateBuffer <- replicateM(mkRegU);
	Reg#(Bit#(8)) issueCnt <- mkReg(0);
	Reg#(Bool) computeOn <- mkReg(False);
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

	// [STAGE 1] Hold the input frame until the final channel leaves the pipeline.
	rule process1 ( !computeOn && !outputReadyOn && inputQ.notEmpty );
		issueCnt <= 0;
		computeOn <= True;
	endrule

	// [STAGE 2] Select within fixed groups of eight, then register candidates.
	rule process2 ( computeOn && issueCnt < 128 );
		Bit#(7) ch = truncate(issueCnt);
		Bit#(3) lowIndex = ch[2:0];
		Vector#(16, SwayValue) xCandidates = newVector;
		Vector#(16, SwayValue) gateCandidates = newVector;
		for ( Integer group = 0; group < 16; group = group + 1 ) begin
			Vector#(8, SwayValue) xGroup = newVector;
			Vector#(8, SwayValue) gateGroup = newVector;
			for ( Integer lane = 0; lane < 8; lane = lane + 1 ) begin
				xGroup[lane] = inputR.data[group * 8 + lane];
				gateGroup[lane] = inputR.data[128 + group * 8 + lane];
			end
			xCandidates[group] = xGroup[lowIndex];
			gateCandidates[group] = gateGroup[lowIndex];
		end
		select8Q.enq(SwayConvSelect8 {channel: ch, first: inputR.first,
			x: xCandidates, gate: gateCandidates});
		issueCnt <= issueCnt + 1;
	endrule

	// [STAGE 3] Reduce sixteen registered candidates to four.
	rule process3;
		let item = select8Q.first;
		select8Q.deq;
		Bit#(2) middleIndex = item.channel[4:3];
		Vector#(4, SwayValue) xCandidates = newVector;
		Vector#(4, SwayValue) gateCandidates = newVector;
		for ( Integer group = 0; group < 4; group = group + 1 ) begin
			Vector#(4, SwayValue) xGroup = newVector;
			Vector#(4, SwayValue) gateGroup = newVector;
			for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
				xGroup[lane] = item.x[group * 4 + lane];
				gateGroup[lane] = item.gate[group * 4 + lane];
			end
			xCandidates[group] = xGroup[middleIndex];
			gateCandidates[group] = gateGroup[middleIndex];
		end
		select4Q.enq(SwayConvSelect4 {channel: item.channel, first: item.first,
			x: xCandidates, gate: gateCandidates});
	endrule

	// [STAGE 4] Final 4:1 selection and aligned weight/history requests.
	rule process4;
		let item = select4Q.first;
		select4Q.deq;
		Bit#(2) highIndex = item.channel[6:5];
		weights.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: item.channel, datain: 0});
		history.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: item.channel, datain: 0});
		readQ.enq(SwayConvInput {channel: item.channel, first: item.first,
			x: item.x[highIndex], gate: item.gate[highIndex]});
	endrule

	// [STAGE 5] Register operands; only first-tagged tokens clear history.
	rule process5;
		let packedW <- weights.portA.response.get;
		let packedH <- history.portA.response.get;
		let item = readQ.first;
		readQ.deq;
		Vector#(5, SwayValue) w = unpack(packedW);
		Vector#(3, SwayValue) h = unpack(packedH);
		if ( item.first ) h = replicate(0);
		Vector#(4, SwayValue) samples = newVector;
		Vector#(4, SwayValue) taps = newVector;
		for ( Integer k = 0; k < 3; k = k + 1 ) samples[k] = h[k];
		samples[3] = item.x;
		for ( Integer k = 0; k < 4; k = k + 1 ) taps[k] = w[k];
		SwayAcc bias = signExtend(w[4]);
		operandQ.enq(SwayConvOperands {channel: item.channel, gate: item.gate,
			samples: samples, weights: taps, bias: bias << 11});
		Vector#(3, SwayValue) nextHistory = newVector;
		nextHistory[0] = h[1];
		nextHistory[1] = h[2];
		nextHistory[2] = item.x;
		history.portB.request.put(BRAMRequest {write: True, responseOnWrite: False,
			address: item.channel, datain: pack(nextHistory)});
	endrule

	// [STAGE 6] Four parallel products, with no reduction or LUT address logic.
	rule process6;
		let item = operandQ.first;
		operandQ.deq;
		Vector#(4, Int#(32)) products = newVector;
		for ( Integer k = 0; k < 4; k = k + 1 ) begin
			products[k] = swayProduct(item.samples[k], item.weights[k]);
		end
		productQ.enq(SwayConvProducts {channel: item.channel, gate: item.gate,
			products: products, bias: item.bias});
		mulCnt <= mulCnt + 4;
	endrule

	// [STAGE 7] First level of a full-width, registered reduction tree.
	rule process7;
		let item = productQ.first;
		productQ.deq;
		Vector#(2, SwayAcc) sums = newVector;
		for ( Integer k = 0; k < 2; k = k + 1 ) begin
			SwayAcc left = signExtend(item.products[k * 2]);
			SwayAcc right = signExtend(item.products[k * 2 + 1]);
			sums[k] = left + right;
		end
		pairQ.enq(SwayConvPairs {channel: item.channel, gate: item.gate,
			sums: sums, bias: item.bias});
	endrule

	// [STAGE 8] Combine tap pairs without narrowing intermediate results.
	rule process8;
		let item = pairQ.first;
		pairQ.deq;
		tapSumQ.enq(SwayConvTapSum {channel: item.channel, gate: item.gate,
			sum: item.sums[0] + item.sums[1], bias: item.bias});
	endrule

	// [STAGE 9] Bias addition is separated from fixed-point conversion.
	rule process9;
		let item = tapSumQ.first;
		tapSumQ.deq;
		sumQ.enq(SwayConvSum {channel: item.channel, gate: item.gate,
			sum: item.sum + item.bias});
	endrule

	// [STAGE 10] Preserve the original arithmetic shift and signed saturation.
	rule process10;
		let item = sumQ.first;
		sumQ.deq;
		activationQ.enq(SwayConvActivation {channel: item.channel, gate: item.gate,
			x: swayRound(item.sum, 11)});
	endrule

	// [STAGE 11] Paired lookup requests and channel metadata advance atomically.
	rule process11;
		let item = activationQ.first;
		activationQ.deq;
		activation.put(item.x);
		gateActivation.put(item.gate);
		resultQ.enq(item.channel);
	endrule

	// [STAGE 12] Retire in order. Channel 127 implies all earlier work drained.
	rule process12 ( computeOn );
		let x <- activation.get;
		let gate <- gateActivation.get;
		let ch = resultQ.first;
		resultQ.deq;
		for ( Integer i = 0; i < 128; i = i + 1 ) begin
			if ( ch == fromInteger(i) ) begin
				xBuffer[i] <= x;
				gateBuffer[i] <= gate;
			end
		end
		if ( ch == 127 ) begin
			inputQ.deq;
			outputFirstR <= inputR.first;
			outputTagR <= inputR.tag;
			outputReadyOn <= True;
			computeOn <= False;
		end
	endrule

	method Action put(SwayFrame#(256) x);
		inputQ.enq(x);
	endmethod
	method ActionValue#(SwayConvFrame) get if ( outputReadyOn );
		outputReadyOn <= False;
		return SwayConvFrame {first: outputFirstR, tag: outputTagR,
			x: readVReg(xBuffer), gate: readVReg(gateBuffer)};
	endmethod
	method Bool busy = computeOn;
	method SwayStats stats = SwayStats {cycles: cycleCnt, busyCycles: busyCnt,
		mulCount: mulCnt, inputEmptyCycles: emptyCnt, outputFullCycles: blockedCnt};
endmodule
endpackage
