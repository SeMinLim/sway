package SwayBaseline;
import Vector::*;
import FIFOF::*;
import SwayTypes::*;
import SwayLinear::*;
import SwayConv::*;
import SwayNonlinear::*;
import SwayScan::*;

interface SwayBaselineIfc;
	method Action put(SwayFrame#(64) x);
	method ActionValue#(SwayFrame#(64)) get;
	method Vector#(7, SwayStats) stats;
	method Bit#(7) activeStages;
endinterface

// Token-overlapped stage organization inspired by eMamba Section 4.4.
// This is a reimplementation for the MambaLite-Micro block, not author RTL.
module mkSwayBaseline(SwayBaselineIfc);
	SwayVectorIfc#(64, 256) inputProjection <- mkSwayLinear("data/in_proj.hex");
	SwayConvIfc convolution <- mkSwayConv;
	SwayVectorIfc#(128, 36) parameterProjection <- mkSwayLinear("data/x_proj.hex");
	SwayVectorIfc#(4, 128) deltaProjection <- mkSwayLinear("data/dt_proj.hex");
	SwayVectorIfc#(128, 128) softplus <- mkSwayActivation("data/softplus.hex");
	SwayScanIfc scan <- mkSwayScan;
	SwayVectorIfc#(128, 64) outputProjection <- mkSwayLinear("data/out_proj.hex");
	FIFOF#(SwayConvFrame) originalQ <- mkSizedFIFOF(2);
	FIFOF#(SwayParamMeta) parametersQ <- mkSizedFIFOF(2);

	rule process1;
		let x <- inputProjection.get;
		convolution.put(x);
	endrule
	rule process2;
		let x <- convolution.get;
		parameterProjection.put(SwayFrame {first: x.first, tag: x.tag, data: x.x});
		originalQ.enq(x);
	endrule
	rule process3;
		let p <- parameterProjection.get;
		let original = originalQ.first;
		originalQ.deq;
		Vector#(4, SwayValue) dt = newVector;
		Vector#(16, SwayValue) b = newVector;
		Vector#(16, SwayValue) c = newVector;
		for ( Integer i = 0; i < 4; i = i + 1 ) dt[i] = p.data[i];
		for ( Integer i = 0; i < 16; i = i + 1 ) begin
			b[i] = p.data[4+i];
			c[i] = p.data[20+i];
		end
		deltaProjection.put(SwayFrame {first: p.first, tag: p.tag, data: dt});
		parametersQ.enq(SwayParamMeta {inputFrame: original, b: b, c: c});
	endrule
	rule process4;
		let x <- deltaProjection.get;
		softplus.put(x);
	endrule
	rule process5;
		let delta <- softplus.get;
		let meta = parametersQ.first;
		parametersQ.deq;
		scan.put(SwayScanFrame {meta: meta, delta: delta.data});
	endrule
	rule process6;
		let x <- scan.get;
		outputProjection.put(x);
	endrule
	method Action put(SwayFrame#(64) x);
		inputProjection.put(x);
	endmethod
	method ActionValue#(SwayFrame#(64)) get;
		let x <- outputProjection.get;
		return x;
	endmethod
	method Vector#(7, SwayStats) stats;
		return vec(inputProjection.stats, convolution.stats, parameterProjection.stats,
			deltaProjection.stats, softplus.stats, scan.stats, outputProjection.stats);
	endmethod
	method Bit#(7) activeStages;
		return {pack(outputProjection.busy), pack(scan.busy), pack(softplus.busy),
			pack(deltaProjection.busy), pack(parameterProjection.busy),
			pack(convolution.busy), pack(inputProjection.busy)};
	endmethod
endmodule
endpackage
