package SwayScan;
import Vector::*;
import FIFO::*;
import FIFOF::*;
import BRAM::*;
import GetPut::*;
import SwayTypes::*;
import SwayNonlinear::*;

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
	SwayValue injection;
} SwayScanInjection deriving (Bits, Eq);

typedef struct {
	SwayScanProduct meta;
	SwayValue state;
} SwayScanUpdated deriving (Bits, Eq);

typedef struct {
	SwayScanProduct meta;
	Int#(32) contribution;
} SwayScanContribution deriving (Bits, Eq);

interface SwayScanIfc;
	method Action put(SwayScanFrame x);
	method ActionValue#(SwayFrame#(128)) get;
	method SwayStats stats;
	method Bool busy;
endinterface

module mkSwayScan(SwayScanIfc);
	FIFOF#(SwayScanFrame) inputQ <- mkSizedFIFOF(1);
	FIFOF#(SwayFrame#(128)) outputQ <- mkSizedFIFOF(1);
	FIFO#(Bit#(11)) readQ <- mkSizedFIFO(4);
	FIFO#(Tuple2#(SwayScanProduct, SwayValue)) productQ <- mkSizedFIFO(4);
	FIFO#(SwayScanInjection) injectionQ <- mkSizedFIFO(4);
	FIFO#(SwayScanUpdated) updatedQ <- mkSizedFIFO(4);
	FIFO#(SwayScanContribution) contributionQ <- mkSizedFIFO(4);
	SwayLutIfc decayLut <- mkSwayLut("data/exp.hex");
	BRAM_Configure cfg = defaultValue;
	cfg.memorySize = 2048;
	cfg.latency = 1;
	cfg.loadFormat = tagged Hex "data/scan.hex";
	BRAM1Port#(Bit#(11), Bit#(32)) constants <- mkBRAM1Server(cfg);
	BRAM_Configure stateCfg = defaultValue;
	stateCfg.memorySize = 2048;
	stateCfg.latency = 1;
	BRAM2Port#(Bit#(11), SwayValue) stateMemory <- mkBRAM2Server(stateCfg);
	Reg#(SwayScanFrame) inputR <- mkRegU;
	Reg#(Vector#(128, SwayValue)) outputR <- mkRegU;
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
		if ( outputQ.notEmpty ) blockedCnt <= blockedCnt + 1;
	endrule
	// [STAGE 1] Independent diagonal modes, one request per cycle.
	rule process1 ( !computeOn );
		inputR <= inputQ.first;
		inputQ.deq;
		issueCnt <= 0;
		computeOn <= True;
	endrule
	rule process2 ( computeOn && issueCnt < 2048 );
		Bit#(11) idx = truncate(issueCnt);
		constants.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: idx, datain: 0});
		stateMemory.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: idx, datain: 0});
		readQ.enq(idx);
		issueCnt <= issueCnt + 1;
	endrule
	// [STAGE 2] delta*A, delta*B, D*x. Static A=-exp(A_log) is exported offline.
	rule process3;
		let packedW <- constants.portA.response.get;
		let previous <- stateMemory.portA.response.get;
		let idx = readQ.first;
		readQ.deq;
		Bit#(7) ch = truncate(idx >> 4);
		Bit#(4) mode = truncate(idx);
		Vector#(2, SwayValue) w = unpack(packedW);
		let frame = inputR.meta.inputFrame;
		let delta = inputR.delta[ch];
		let x = frame.x[ch];
		SwayScanProduct meta = SwayScanProduct {index: idx,
			state: frame.first ? 0 : previous,
			deltaB: swayMul(delta, inputR.meta.b[mode]), c: inputR.meta.c[mode],
			x: x, gate: frame.gate[ch], skip: swayMul(w[1], x)};
		productQ.enq(tuple2(meta, swayMul(delta, w[0])));
	endrule
	// [STAGE 3] Input injection and synchronous exponential approximation.
	rule process4;
		let meta = tpl_1(productQ.first);
		let deltaA = tpl_2(productQ.first);
		productQ.deq;
		decayLut.put(deltaA);
		injectionQ.enq(SwayScanInjection {meta: meta, injection: swayMul(meta.deltaB, meta.x)});
	endrule
	// [STAGE 4] h'=decay*h+injection, committed to SRAM in issue order.
	rule process5;
		let decay <- decayLut.get;
		let item = injectionQ.first;
		injectionQ.deq;
		let h = swayAdd(swayMul(decay, item.meta.state), item.injection);
		stateMemory.portB.request.put(BRAMRequest {write: True, responseOnWrite: False,
			address: item.meta.index, datain: h});
		updatedQ.enq(SwayScanUpdated {meta: item.meta, state: h});
	endrule
	// [STAGE 5] Readout contribution with a full-width product.
	rule process6;
		let item = updatedQ.first;
		updatedQ.deq;
		contributionQ.enq(SwayScanContribution {meta: item.meta,
			contribution: swayProduct(item.state, item.meta.c)});
	endrule
	// [STAGE 6] Reduce 16 modes per channel; then gate and emit.
	rule process7;
		let item = contributionQ.first;
		contributionQ.deq;
		Bit#(4) mode = truncate(item.meta.index);
		Bit#(7) ch = truncate(item.meta.index >> 4);
		SwayAcc sum = signExtend(item.contribution);
		if ( mode == 0 ) begin
			SwayAcc skip = signExtend(item.meta.skip);
			sum = sum + (skip << swayFraction);
		end else sum = sum + sumR;
		sumR <= sum;
		mulCnt <= mulCnt + (mode == 15 ? 7 : 6);
		if ( mode == 15 ) begin
			Vector#(128, SwayValue) nextOutput = outputR;
			nextOutput[ch] = swayMul(swayRound(sum), item.meta.gate);
			outputR <= nextOutput;
			if ( ch == 127 ) begin
				outputQ.enq(SwayFrame {first: inputR.meta.inputFrame.first,
					tag: inputR.meta.inputFrame.tag, data: nextOutput});
				computeOn <= False;
			end
		end
	endrule
	method Action put(SwayScanFrame x);
		inputQ.enq(x);
	endmethod
	method ActionValue#(SwayFrame#(128)) get;
		let x = outputQ.first;
		outputQ.deq;
		return x;
	endmethod
	method Bool busy = computeOn;
	method SwayStats stats = SwayStats {cycles: cycleCnt, busyCycles: busyCnt,
		mulCount: mulCnt, inputEmptyCycles: emptyCnt, outputFullCycles: blockedCnt};
endmodule
endpackage
