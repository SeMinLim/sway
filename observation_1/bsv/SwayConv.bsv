package SwayConv;
import Vector::*;
import FIFO::*;
import FIFOF::*;
import BRAM::*;
import GetPut::*;
import SwayTypes::*;
import SwayNonlinear::*;

interface SwayConvIfc;
	method Action put(SwayFrame#(256) x);
	method ActionValue#(SwayConvFrame) get;
	method SwayStats stats;
	method Bool busy;
endinterface

(* synthesize *)
module mkSwayConv(SwayConvIfc);
	FIFOF#(SwayFrame#(256)) inputQ <- mkSizedFIFOF(1);
	// Reuse the result vector as the output holding buffer.
	Reg#(Bool) outputReadyOn <- mkReg(False);
	Reg#(Bool) outputFirstR <- mkReg(False);
	Reg#(Bit#(16)) outputTagR <- mkReg(0);
	FIFO#(Bit#(7)) readQ <- mkSizedFIFO(4);
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
	Reg#(Vector#(128, SwayValue)) xR <- mkRegU;
	Reg#(Vector#(128, SwayValue)) gateR <- mkRegU;
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
	// [STAGE 1] Read one channel's four taps and three retained samples.
	rule process1 ( !computeOn && !outputReadyOn && inputQ.notEmpty );
		issueCnt <= 0;
		computeOn <= True;
	endrule
	rule process2 ( computeOn && issueCnt < 128 );
		Bit#(7) ch = truncate(issueCnt);
		weights.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: ch, datain: 0});
		history.portA.request.put(BRAMRequest {write: False, responseOnWrite: False,
			address: ch, datain: 0});
		readQ.enq(ch);
		issueCnt <= issueCnt + 1;
	endrule
	// [STAGE 2] Causal convolution. Only first-tagged tokens clear history.
	rule process3;
		let packedW <- weights.portA.response.get;
		let packedH <- history.portA.response.get;
		let ch = readQ.first;
		readQ.deq;
		Vector#(5, SwayValue) w = unpack(packedW);
		Vector#(3, SwayValue) h = unpack(packedH);
		if ( inputR.first ) h = replicate(0);
		SwayValue x = inputR.data[ch];
		SwayAcc sum = signExtend(w[4]);
		sum = sum << 11;
		for ( Integer k = 0; k < 3; k = k + 1 ) begin
			sum = sum + signExtend(swayProduct(h[k], w[k]));
		end
		sum = sum + signExtend(swayProduct(x, w[3]));
		Vector#(3, SwayValue) nextHistory = newVector;
		nextHistory[0] = h[1];
		nextHistory[1] = h[2];
		nextHistory[2] = x;
		history.portB.request.put(BRAMRequest {write: True, responseOnWrite: False,
			address: ch, datain: pack(nextHistory)});
		activation.put(swayRound(sum, 11));
		Bit#(8) gateIdx = zeroExtend(ch) + 128;
		gateActivation.put(inputR.data[gateIdx]);
		resultQ.enq(ch);
		mulCnt <= mulCnt + 4;
	endrule
	// [STAGE 3] Pair the two independent activation results.
	rule process4 ( computeOn );
		let x <- activation.get;
		let gate <- gateActivation.get;
		let ch = resultQ.first;
		resultQ.deq;
		Vector#(128, SwayValue) nextX = xR;
		Vector#(128, SwayValue) nextGate = gateR;
		nextX[ch] = x;
		nextGate[ch] = gate;
		xR <= nextX;
		gateR <= nextGate;
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
		return SwayConvFrame {first: outputFirstR, tag: outputTagR, x: xR, gate: gateR};
	endmethod
	method Bool busy = computeOn;
	method SwayStats stats = SwayStats {cycles: cycleCnt, busyCycles: busyCnt,
		mulCount: mulCnt, inputEmptyCycles: emptyCnt, outputFullCycles: blockedCnt};
endmodule
endpackage
