package SwayScan;
import Vector::*;
import FIFO::*;
import FIFOF::*;
import BRAM::*;
import GetPut::*;

import SwayTypes::*;
import SwayNonlinear::*;

// Register the 128-channel selector at 8:1, 4:1, and 4:1 boundaries.
// B/C use two registered 4:1 levels over the 16 state modes.
typedef struct {
	Bit#(11) index;
	Bool first;
	Vector#(16, SwayValue) delta;
	Vector#(16, SwayValue) x;
	Vector#(16, SwayValue) gate;
	Vector#(4, SwayValue) b;
	Vector#(4, SwayValue) c;
} SwayScanSelect8 deriving (Bits, Eq);

typedef struct {
	Bit#(11) index;
	Bool first;
	Vector#(4, SwayValue) delta;
	Vector#(4, SwayValue) x;
	Vector#(4, SwayValue) gate;
	SwayValue b;
	SwayValue c;
} SwayScanSelect4 deriving (Bits, Eq);

typedef struct {
	Bit#(11) index;
	Bool first;
	SwayValue delta;
	SwayValue x;
	SwayValue gate;
	SwayValue b;
	SwayValue c;
} SwayScanSelected deriving (Bits, Eq);

typedef struct {
	SwayScanSelected selected;
	SwayValue state;
	SwayValue a;
	SwayValue d;
} SwayScanOperands deriving (Bits, Eq);

typedef struct {
	Bit#(11) index;
	SwayValue state;
	SwayValue c;
	SwayValue x;
	SwayValue gate;
	Int#(32) deltaA;
	Int#(32) deltaB;
	Int#(32) skip;
} SwayScanProducts deriving (Bits, Eq);

typedef struct {
	Bit#(11) index;
	SwayValue state;
	SwayValue deltaB;
	SwayValue c;
	SwayValue x;
	SwayValue gate;
	SwayValue skip;
} SwayScanProduct deriving (Bits, Eq);

typedef struct {
	SwayScanProduct meta;
	Int#(32) product;
} SwayScanInjectionProduct deriving (Bits, Eq);

typedef struct {
	SwayScanProduct meta;
	SwayValue injection;
} SwayScanInjection deriving (Bits, Eq);

typedef struct {
	SwayScanInjection item;
	SwayValue decay;
} SwayScanDecay deriving (Bits, Eq);

typedef struct {
	SwayScanInjection item;
	Int#(32) product;
} SwayScanStateProduct deriving (Bits, Eq);

typedef struct {
	SwayScanProduct meta;
	SwayValue retained;
	SwayValue injection;
} SwayScanStateTerms deriving (Bits, Eq);

typedef struct {
	SwayScanProduct meta;
	Int#(17) sum;
} SwayScanStateSum deriving (Bits, Eq);

typedef struct {
	SwayScanProduct meta;
	SwayValue state;
} SwayScanUpdated deriving (Bits, Eq);

typedef struct {
	SwayScanProduct meta;
	Int#(32) contribution;
} SwayScanContribution deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	SwayAcc sum;
	SwayValue gate;
} SwayScanReduced deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	SwayValue readout;
	SwayValue gate;
} SwayScanGateOperands deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	Int#(32) product;
} SwayScanGateProduct deriving (Bits, Eq);

typedef struct {
	Bit#(7) channel;
	SwayValue value;
} SwayScanOutput deriving (Bits, Eq);

interface SwayScanIfc;
	method Action put(SwayScanFrame x);
	method ActionValue#(SwayFrame#(128)) get;
	method SwayStats stats;
	method Bool busy;
endinterface

(* synthesize *)
module mkSwayScan(SwayScanIfc);
	FIFOF#(SwayScanFrame) inputQ <- mkSizedFIFOF(1);
	Reg#(Bool) outputReadyOn <- mkReg(False);
	Reg#(Bool) outputFirstR <- mkReg(False);
	Reg#(Bit#(16)) outputTagR <- mkReg(0);

	// Two-entry, non-bypass FIFOs are real register boundaries and allow
	// concurrent producer/consumer operation. SRAM request metadata has
	// four entries, matching the existing bounded SRAM response buffering.
	FIFO#(SwayScanSelect8) select8Q <- mkFIFO;
	FIFO#(SwayScanSelect4) select4Q <- mkFIFO;
	FIFO#(SwayScanSelected) selectedQ <- mkFIFO;
	FIFO#(SwayScanSelected) readQ <- mkSizedFIFO(4);
	FIFO#(SwayScanOperands) operandsQ <- mkFIFO;
	FIFO#(SwayScanProducts) productsQ <- mkFIFO;
	FIFO#(Tuple2#(SwayScanProduct, SwayValue)) productQ <- mkFIFO;
	FIFO#(SwayScanInjectionProduct) injectionProductQ <- mkFIFO;
	FIFO#(SwayScanInjection) injectionQ <- mkFIFO;
	FIFO#(SwayScanDecay) decayQ <- mkFIFO;
	FIFO#(SwayScanStateProduct) stateProductQ <- mkFIFO;
	FIFO#(SwayScanStateTerms) stateTermsQ <- mkFIFO;
	FIFO#(SwayScanStateSum) stateSumQ <- mkFIFO;
	FIFO#(SwayScanUpdated) updatedQ <- mkFIFO;
	FIFO#(SwayScanUpdated) writebackQ <- mkFIFO;
	FIFO#(SwayScanContribution) contributionQ <- mkFIFO;
	FIFO#(SwayScanReduced) reducedQ <- mkFIFO;
	FIFO#(SwayScanGateOperands) gateOperandsQ <- mkFIFO;
	FIFO#(SwayScanGateProduct) gateProductQ <- mkFIFO;
	FIFO#(SwayScanOutput) completedQ <- mkFIFO;

	SwayLutIfc decayLut <- mkSwayDecayLut;
	BRAM_Configure cfg = defaultValue;
	cfg.memorySize = valueOf(SwayInner) * valueOf(SwayState);
	cfg.latency = 1;
	cfg.loadFormat = tagged Hex "data/scan.hex";
	BRAM1Port#(Bit#(11), Bit#(32)) constants <- mkBRAM1Server(cfg);
	BRAM_Configure stateCfg = defaultValue;
	stateCfg.memorySize = valueOf(SwayInner) * valueOf(SwayState);
	stateCfg.latency = 1;
	BRAM2Port#(Bit#(11), SwayValue) stateMemory <- mkBRAM2Server(stateCfg);
	let inputR = inputQ.first;
	Vector#(128, Reg#(SwayValue)) outputBuffer <- replicateM(mkRegU);
	Reg#(Bit#(12)) issueCnt <- mkReg(0);
	Reg#(Bool) computeOn <- mkReg(False);
	Reg#(SwayAcc) sumR <- mkReg(0);
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

	// [STAGE 1] Retain the token until the final gated channel is written.
	rule process1 ( !computeOn && !outputReadyOn && inputQ.notEmpty );
		issueCnt <= 0;
		computeOn <= True;
	endrule

	// [STAGE 2] First registered channel/mode selection level.
	rule process2 ( computeOn && issueCnt < fromInteger(valueOf(SwayInner) * valueOf(SwayState)) );
		Bit#(11) idx = truncate(issueCnt);
		Bit#(3) lowChannel = idx[6:4];
		Bit#(2) lowMode = idx[1:0];
		let frame = inputR.meta.inputFrame;
		Vector#(16, SwayValue) delta = newVector;
		Vector#(16, SwayValue) x = newVector;
		Vector#(16, SwayValue) gate = newVector;
		Vector#(4, SwayValue) b = newVector;
		Vector#(4, SwayValue) c = newVector;
		for ( Integer groupIdx = 0; groupIdx < 16; groupIdx = groupIdx + 1 ) begin
			Vector#(8, SwayValue) deltaBank = newVector;
			Vector#(8, SwayValue) xBank = newVector;
			Vector#(8, SwayValue) gateBank = newVector;
			for ( Integer lane = 0; lane < 8; lane = lane + 1 ) begin
				deltaBank[lane] = inputR.delta[groupIdx * 8 + lane];
				xBank[lane] = frame.x[groupIdx * 8 + lane];
				gateBank[lane] = frame.gate[groupIdx * 8 + lane];
			end
			delta[groupIdx] = deltaBank[lowChannel];
			x[groupIdx] = xBank[lowChannel];
			gate[groupIdx] = gateBank[lowChannel];
		end
		for ( Integer groupIdx = 0; groupIdx < 4; groupIdx = groupIdx + 1 ) begin
			Vector#(4, SwayValue) bBank = newVector;
			Vector#(4, SwayValue) cBank = newVector;
			for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
				bBank[lane] = inputR.meta.b[groupIdx * 4 + lane];
				cBank[lane] = inputR.meta.c[groupIdx * 4 + lane];
			end
			b[groupIdx] = bBank[lowMode];
			c[groupIdx] = cBank[lowMode];
		end
		select8Q.enq(SwayScanSelect8 {index: idx, first: frame.first,
			delta: delta, x: x, gate: gate, b: b, c: c});
		issueCnt <= issueCnt + 1;
	endrule

	// [STAGE 3] Four candidates per channel operand, one B/C pair.
	rule process3;
		let item = select8Q.first;
		select8Q.deq;
		Bit#(2) middleChannel = item.index[8:7];
		Bit#(2) highMode = item.index[3:2];
		Vector#(4, SwayValue) delta = newVector;
		Vector#(4, SwayValue) x = newVector;
		Vector#(4, SwayValue) gate = newVector;
		for ( Integer groupIdx = 0; groupIdx < 4; groupIdx = groupIdx + 1 ) begin
			Vector#(4, SwayValue) deltaBank = newVector;
			Vector#(4, SwayValue) xBank = newVector;
			Vector#(4, SwayValue) gateBank = newVector;
			for ( Integer lane = 0; lane < 4; lane = lane + 1 ) begin
				deltaBank[lane] = item.delta[groupIdx * 4 + lane];
				xBank[lane] = item.x[groupIdx * 4 + lane];
				gateBank[lane] = item.gate[groupIdx * 4 + lane];
			end
			delta[groupIdx] = deltaBank[middleChannel];
			x[groupIdx] = xBank[middleChannel];
			gate[groupIdx] = gateBank[middleChannel];
		end
		select4Q.enq(SwayScanSelect4 {index: item.index, first: item.first,
			delta: delta, x: x, gate: gate, b: item.b[highMode], c: item.c[highMode]});
	endrule

	// [STAGE 4] Final channel selection, before any SRAM read or multiply.
	rule process4;
		let item = select4Q.first;
		select4Q.deq;
		Bit#(2) highChannel = item.index[10:9];
		selectedQ.enq(SwayScanSelected {index: item.index, first: item.first,
			delta: item.delta[highChannel], x: item.x[highChannel],
			gate: item.gate[highChannel], b: item.b, c: item.c});
	endrule

	// [STAGE 5] Ordered constants/state requests with matching operands.
	rule process5;
		let item = selectedQ.first;
		selectedQ.deq;
		constants.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: item.index, datain: 0});
		stateMemory.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: item.index, datain: 0});
		readQ.enq(item);
	endrule

	// [STAGE 6] Register SRAM responses; no arithmetic on this path.
	rule process6;
		let packedW <- constants.portA.response.get;
		let previous <- stateMemory.portA.response.get;
		let item = readQ.first;
		readQ.deq;
		Vector#(2, SwayValue) w = unpack(packedW);
		operandsQ.enq(SwayScanOperands {selected: item,
			state: item.first ? 0 : previous, a: w[0], d: w[1]});
	endrule

	// [STAGE 7] Three full-width products, without shifts or saturation.
	rule process7;
		let item = operandsQ.first;
		operandsQ.deq;
		let s = item.selected;
		productsQ.enq(SwayScanProducts {index: s.index, state: item.state,
			c: s.c, x: s.x, gate: s.gate,
			deltaA: swayProduct(s.delta, item.a),
			deltaB: swayProduct(s.delta, s.b), skip: swayProduct(item.d, s.x)});
	endrule

	// [STAGE 8] Preserve each original fixed-point conversion boundary.
	rule process8;
		let item = productsQ.first;
		productsQ.deq;
		SwayScanProduct meta = SwayScanProduct {index: item.index, state: item.state,
			deltaB: swayRound(signExtend(item.deltaB), 13), c: item.c,
			x: item.x, gate: item.gate, skip: swayRound(signExtend(item.skip), 13)};
		productQ.enq(tuple2(meta, swayRound(signExtend(item.deltaA), 15)));
	endrule

	// [STAGE 9] Issue decay lookup and register the injection product.
	rule process9;
		let meta = tpl_1(productQ.first);
		let deltaA = tpl_2(productQ.first);
		productQ.deq;
		decayLut.put(deltaA);
		injectionProductQ.enq(SwayScanInjectionProduct {meta: meta,
			product: swayProduct(meta.deltaB, meta.x)});
	endrule

	// [STAGE 10] Injection conversion is separate from its multiply.
	rule process10;
		let item = injectionProductQ.first;
		injectionProductQ.deq;
		injectionQ.enq(SwayScanInjection {meta: item.meta,
			injection: swayRound(signExtend(item.product), 10)});
	endrule

	// [STAGE 11] Capture the decay SRAM response before using the DSP.
	rule process11;
		let decay <- decayLut.get;
		let item = injectionQ.first;
		injectionQ.deq;
		decayQ.enq(SwayScanDecay {item: item, decay: decay});
	endrule

	// [STAGE 12] Full-width retained-state product.
	rule process12;
		let item = decayQ.first;
		decayQ.deq;
		stateProductQ.enq(SwayScanStateProduct {item: item.item,
			product: swayProduct(item.decay, item.item.meta.state)});
	endrule

	// [STAGE 13] First saturation MUST precede the injection addition.
	rule process13;
		let item = stateProductQ.first;
		stateProductQ.deq;
		stateTermsQ.enq(SwayScanStateTerms {meta: item.item.meta,
			retained: swayRound(signExtend(item.product), 15), injection: item.item.injection});
	endrule

	// [STAGE 14] Seventeen bits exactly hold the sum of two signed16 terms.
	rule process14;
		let item = stateTermsQ.first;
		stateTermsQ.deq;
		Int#(17) retained = signExtend(item.retained);
		Int#(17) injection = signExtend(item.injection);
		stateSumQ.enq(SwayScanStateSum {meta: item.meta, sum: retained + injection});
	endrule

	// [STAGE 15] Second saturation, after addition and before writeback.
	rule process15;
		let item = stateSumQ.first;
		stateSumQ.deq;
		updatedQ.enq(SwayScanUpdated {meta: item.meta, state: swaySaturate(signExtend(item.sum))});
	endrule

	// [STAGE 16] Commit state in issue order. No next token starts until
	// the final channel has also traversed reduction and output gating.
	rule process16;
		let item = updatedQ.first;
		updatedQ.deq;
		stateMemory.portB.request.put(BRAMRequest {write: True, responseOnWrite: False,
			address: item.meta.index, datain: item.state});
		writebackQ.enq(item);
	endrule

	// [STAGE 17] Full-width C readout product, without accumulation.
	rule process17;
		let item = writebackQ.first;
		writebackQ.deq;
		contributionQ.enq(SwayScanContribution {meta: item.meta,
			contribution: swayProduct(item.state, item.meta.c)});
	endrule

	// [STAGE 18] Preserve the ordered 48-bit reduction across 16 modes.
	// No shift, saturation, or gate multiply is on the feedback path.
	rule process18 ( computeOn );
		let item = contributionQ.first;
		contributionQ.deq;
		Bit#(4) mode = truncate(item.meta.index);
		Bit#(7) ch = truncate(item.meta.index >> 4);
		SwayAcc sum = signExtend(item.contribution);
		if ( mode == 0 ) begin
			SwayAcc skip = signExtend(item.meta.skip);
			sum = sum + (skip << 12);
		end else begin
			sum = sum + sumR;
		end
		sumR <= sum;
		// Same logical operation count as the original implementation.
		mulCnt <= mulCnt + (mode == 15 ? 7 : 6);
		if ( mode == 15 ) begin
			reducedQ.enq(SwayScanReduced {channel: ch, sum: sum, gate: item.meta.gate});
		end
	endrule

	// [STAGE 19] Readout conversion before gating, as in the reference.
	rule process19;
		let item = reducedQ.first;
		reducedQ.deq;
		gateOperandsQ.enq(SwayScanGateOperands {channel: item.channel,
			readout: swayRound(item.sum, 12), gate: item.gate});
	endrule

	// [STAGE 20] Register the gate product independently.
	rule process20;
		let item = gateOperandsQ.first;
		gateOperandsQ.deq;
		gateProductQ.enq(SwayScanGateProduct {channel: item.channel,
			product: swayProduct(item.readout, item.gate)});
	endrule

	// [STAGE 21] Final conversion before the output-buffer write decoder.
	rule process21;
		let item = gateProductQ.first;
		gateProductQ.deq;
		completedQ.enq(SwayScanOutput {channel: item.channel,
			value: swayRound(signExtend(item.product), 13)});
	endrule

	// [STAGE 22] Static write enables; release input only after full drain.
	rule process22 ( computeOn );
		let item = completedQ.first;
		completedQ.deq;
		for ( Integer i = 0; i < valueOf(SwayInner); i = i + 1 ) begin
			if ( item.channel == fromInteger(i) ) outputBuffer[i] <= item.value;
		end
		if ( item.channel == fromInteger(valueOf(SwayInner) - 1) ) begin
			inputQ.deq;
			outputFirstR <= inputR.meta.inputFrame.first;
			outputTagR <= inputR.meta.inputFrame.tag;
			outputReadyOn <= True;
			computeOn <= False;
		end
	endrule

	method Action put(SwayScanFrame x);
		inputQ.enq(x);
	endmethod
	method ActionValue#(SwayFrame#(128)) get if ( outputReadyOn );
		outputReadyOn <= False;
		return SwayFrame {first: outputFirstR, tag: outputTagR, data: readVReg(outputBuffer)};
	endmethod
	method Bool busy = computeOn;
	method SwayStats stats = SwayStats {cycles: cycleCnt, busyCycles: busyCnt,
		mulCount: mulCnt, inputEmptyCycles: emptyCnt, outputFullCycles: blockedCnt};
endmodule
endpackage
