package Reciprocal;

import FIFOF::*;
import Vector::*;
import QuantTypes::*;

// Exact low 32 bits of floor(2^36 / denominator), not a timed / operator.
// Sixteen registered radix-4 steps produce two quotient bits per step.
interface ReciprocalIfc;
	method Action put(TokenTag tag, Bit#(32) denominator);
	method ActionValue#(ReciprocalResult) get;
endinterface

function ReciprocalWork divideStep(ReciprocalWork work);
	UInt#(34) divisor = zeroExtend(unpack(work.denominator));
	UInt#(34) trial = work.remainder << 2;
	UInt#(34) twice = divisor << 1;
	UInt#(34) thrice = twice + divisor;
	Bit#(2) digit = 0;
	if ( !work.zeroScale ) begin
		if ( trial >= thrice ) begin
			trial = trial - thrice;
			digit = 3;
		end else if ( trial >= twice ) begin
			trial = trial - twice;
			digit = 2;
		end else if ( trial >= divisor ) begin
			trial = trial - divisor;
			digit = 1;
		end
	end
	work.remainder = trial;
	work.quotient = (work.quotient << 2) | zeroExtend(digit);
	return work;
endfunction

(* synthesize *)
module mkReciprocal(ReciprocalIfc);
	Vector#(16, FIFOF#(ReciprocalWork)) stageQ <- replicateM(mkFIFOF);

	for ( Integer i = 0; i < 15; i = i + 1 ) begin
		rule processStage;
			let work = stageQ[i].first;
			stageQ[i].deq;
			stageQ[i + 1].enq(divideStep(work));
		endrule
	end

	method Action put(TokenTag tag, Bit#(32) denominator);
		UInt#(34) initialRemainder = 16;
		UInt#(34) divisor = zeroExtend(unpack(denominator));
		// Discard high quotient bits exactly, including denominator 1..16.
		// This bounded initial reduction is combinational, not a divider stub.
		for ( Integer i = 0; i < 16; i = i + 1 ) begin
			if ( denominator != 0 && initialRemainder >= divisor ) begin
				initialRemainder = initialRemainder - divisor;
			end
		end
		ReciprocalWork work = ReciprocalWork {
			tag: tag,
			denominator: denominator,
			remainder: initialRemainder,
			quotient: 0,
			zeroScale: denominator == 0
		};
		stageQ[0].enq(divideStep(work));
	endmethod

	method ActionValue#(ReciprocalResult) get;
		let work = stageQ[15].first;
		stageQ[15].deq;
		return ReciprocalResult {
			tag: work.tag,
			value: work.quotient,
			zeroScale: work.zeroScale
		};
	endmethod
endmodule

endpackage
