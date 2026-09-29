package SwayMultiply;

import FIFOF::*;

interface RawMultiplyIfc;
	method Action putA(Bit#(18) value);
	method Action putB(Bit#(18) value);
	method Bit#(36) product;
endinterface

import "BVI" sway_mult18x18d =
module mkRawMultiply#(Clock clk, Reset rstn)(RawMultiplyIfc);
	default_clock no_clock;
	default_reset no_reset;
	input_clock (clk) = clk;
	input_reset (rstn) = rstn;
	method dataout product clocked_by(clk) reset_by(no_reset);
	method putA(dataax) enable((*inhigh*) enableA) clocked_by(clk) reset_by(no_reset);
	method putB(dataay) enable((*inhigh*) enableB) clocked_by(clk) reset_by(no_reset);
	schedule (putA, putB, product) CF (putA, putB, product);
endmodule

// Cycle-equivalent model of this fixed DSP register configuration for Bluesim.
module mkRawMultiplyModel(RawMultiplyIfc);
	Wire#(Bit#(18)) aWire <- mkDWire(0);
	Wire#(Bit#(18)) bWire <- mkDWire(0);
	Reg#(Int#(18)) aR <- mkReg(0);
	Reg#(Int#(18)) bR <- mkReg(0);
	Reg#(Int#(36)) productR <- mkReg(0);
	Reg#(Int#(36)) outputR <- mkReg(0);
	rule advanceModel;
		aR <= unpack(aWire);
		bR <= unpack(bWire);
		productR <= signExtend(aR) * signExtend(bR);
		outputR <= productR;
	endrule
	method Action putA(Bit#(18) value);
		aWire <= value;
	endmethod
	method Action putB(Bit#(18) value);
		bWire <= value;
	endmethod
	method Bit#(36) product;
		return pack(outputR);
	endmethod
endmodule

interface MultiplyIfc;
	method Action put(Int#(18) a, Int#(18) b);
	method ActionValue#(Int#(36)) get;
endinterface

module mkMultiply(MultiplyIfc);
	Clock clk <- exposeCurrentClock;
	Reset rstn <- exposeCurrentReset;
	RawMultiplyIfc dsp;
	if ( genC ) begin
		dsp <- mkRawMultiplyModel;
	end else begin
		dsp <- mkRawMultiply(clk, rstn);
	end
	// Operands on cycles without put are unobservable: only validR captures.
	// Leave them unspecified so put readiness does not gate DSP data inputs.
	Wire#(Bit#(18)) aWire <- mkDWire(?);
	Wire#(Bit#(18)) bWire <- mkDWire(?);
	Wire#(Bit#(1)) validWire <- mkDWire(0);
	Reg#(Bit#(3)) validR <- mkReg(0);
	FIFOF#(Int#(36)) resultQ <- mkSizedFIFOF(8);
	Reg#(Bit#(4)) acceptedCnt <- mkReg(0);
	Reg#(Bit#(4)) consumedCnt <- mkReg(0);
	// The DSP advances every cycle; valid products reserve result capacity at put.
	// Keep capture separate so FIFO readiness cannot gate the native pipeline.
	rule advance;
		dsp.putA(aWire);
		dsp.putB(bWire);
		validR <= (validR << 1) | zeroExtend(validWire);
	endrule
	rule capture ( validR[2] == 1 );
		resultQ.enq(unpack(dsp.product));
	endrule
	method Action put(Int#(18) a, Int#(18) b) if ( acceptedCnt - consumedCnt < 8 );
		aWire <= pack(a);
		bWire <= pack(b);
		validWire <= 1;
		acceptedCnt <= acceptedCnt + 1;
	endmethod
	method ActionValue#(Int#(36)) get;
		let value = resultQ.first;
		resultQ.deq;
		consumedCnt <= consumedCnt + 1;
		return value;
	endmethod
endmodule

endpackage
