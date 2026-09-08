package QuantMultiplier;

import FIFOF::*;
import Vector::*;
import QuantTypes::*;

interface QuantMultiplierIfc;
	method Action put(TokenTag tag, MultiplyOp op,
		FeatureVec a, Vector#(LaneNum, Int#(64)) b, Bit#(6) shift);
	method ActionValue#(MultiplyResult) get;
endinterface

(* synthesize *)
module mkQuantMultiplier(QuantMultiplierIfc);
	FIFOF#(MultiplyRaw) productQ <- mkFIFOF;
	FIFOF#(MultiplyResult) resultQ <- mkFIFOF;

	// [STAGE 2] Apply fixed-point truncation to registered products.
	rule process2;
		let raw = productQ.first;
		productQ.deq;
		FeatureVec values = replicate(0);
		for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
			values[i] = featureProduct(raw.product[i], raw.shift);
		end
		resultQ.enq(MultiplyResult {tag: raw.tag, op: raw.op, value: values});
	endrule

	// [STAGE 1] All three operations share these four product lanes.
	method Action put(TokenTag tag, MultiplyOp op,
		FeatureVec a, Vector#(LaneNum, Int#(64)) b, Bit#(6) shift);
		Vector#(LaneNum, Int#(64)) products = replicate(0);
		for ( Integer i = 0; i < valueOf(LaneNum); i = i + 1 ) begin
			Int#(64) wideA = signExtend(a[i]);
			products[i] = wideA * b[i];
		end
		productQ.enq(MultiplyRaw {tag: tag, op: op, product: products, shift: shift});
	endmethod

	method ActionValue#(MultiplyResult) get;
		let result = resultQ.first;
		resultQ.deq;
		return result;
	endmethod
endmodule

endpackage
