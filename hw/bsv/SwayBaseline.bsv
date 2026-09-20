package SwayBaseline;

import Vector::*;
import FIFO::*;
import FIFOF::*;

import SwayTypes::*;
import SwayParameters::*;
import SwayLinear::*;
import SwayBlock::*;

// Fixed-weight MARS graph. Each affine/normalization/scan stage has its own
// engine. FIFOs permit adjacent tokens and independent frames to overlap.
module mkSwayBaseline(SwayIfc);
	FIFO#(Int#(8)) inputQ <- mkSizedFIFO(32);
	FIFO#(Int#(8)) outputQ <- mkSizedFIFO(32);
	FIFO#(Vector#(320, Int#(8))) frameQ <- mkFIFO1;
	Reg#(Vector#(320, Int#(8))) inputR <- mkRegU;
	Reg#(Bit#(9)) inputCnt <- mkReg(0);

	LinearIfc#(20, 20) embedding <- mkSwayLinear(0);
	BlockIfc block0 <- mkSwayBlock(0);
	BlockIfc block1 <- mkSwayBlock(1);
	LinearIfc#(320, 20) headHidden <- mkSwayLinear(9);
	LinearIfc#(20, 57) headOutput <- mkSwayLinear(10);

	Reg#(Vector#(320, Int#(8))) frameR <- mkRegU;
	Reg#(Bool) patchOn <- mkReg(False);
	Reg#(Bit#(4)) patchCnt <- mkReg(0);
	Vector#(16, Reg#(Vector#(20, Int#(8)))) headR <- replicateM(mkRegU);
	Reg#(Vector#(57, Int#(8))) resultR <- mkRegU;
	Reg#(Bool) outputOn <- mkReg(False);
	Reg#(Bit#(6)) outputCnt <- mkReg(0);

	//------------------------------------------------------------------------------------
	// [STAGE 1] Collect one HWC frame and form row-major 2x2x5 patches.
	//------------------------------------------------------------------------------------
	rule process1;
		Vector#(320, Int#(8)) nextInput = inputR;
		nextInput[inputCnt] = inputQ.first;
		inputQ.deq;
		inputR <= nextInput;
		if ( inputCnt == 319 ) begin
			frameQ.enq(nextInput);
			inputCnt <= 0;
		end else inputCnt <= inputCnt + 1;
	endrule

	rule process2 ( !patchOn );
		frameR <= frameQ.first;
		frameQ.deq;
		patchCnt <= 0;
		patchOn <= True;
	endrule

	rule process3 ( patchOn );
		Vector#(16, Vector#(20, Int#(8))) patches = newVector;
		for ( Integer p = 0; p < 16; p = p + 1 ) begin
			for ( Integer i = 0; i < 20; i = i + 1 ) begin
				Integer address = (2 * (p / 4) + i / 10) * 40
					+ (2 * (p % 4) + (i / 5) % 2) * 5 + i % 5;
				patches[p][i] = requant(signExtend(frameR[address]), nodeScale("input"), nodeScale("patches"));
			end
		end
		embedding.put(Token { index: patchCnt, data: patches[patchCnt] });
		if ( patchCnt == 15 ) patchOn <= False;
		else patchCnt <= patchCnt + 1;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2] Two dedicated Mamba block pipelines.
	//------------------------------------------------------------------------------------
	rule process4;
		let value <- embedding.get;
		block0.put(value);
	endrule

	rule process5;
		let value <- block0.get;
		block1.put(value);
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 3] Flatten all token positions, regress 57 coordinates, serialize.
	//------------------------------------------------------------------------------------
	rule process6;
		let value <- block1.get;
		Vector#(20, Int#(8)) token = newVector;
		for ( Integer i = 0; i < 20; i = i + 1 ) begin
			token[i] = requant(signExtend(value.data[i]),
				blockScale(1, "residual"), nodeScale("headInput"));
		end
		headR[value.index] <= token;
		if ( value.index == 15 ) begin
			Vector#(320, Int#(8)) flattened = newVector;
			for ( Integer p = 0; p < 16; p = p + 1 ) begin
				Vector#(20, Int#(8)) stored = p == 15 ? token : headR[p];
				for ( Integer i = 0; i < 20; i = i + 1 ) begin
					flattened[p * 20 + i] = stored[i];
				end
			end
			headHidden.put(Token { index: 0, data: flattened });
		end
	endrule

	rule process7;
		Token#(20) value <- headHidden.get;
		for ( Integer i = 0; i < 20; i = i + 1 ) begin
			Int#(8) relu = value.data[i] < 0 ? 0 : value.data[i];
			value.data[i] = requant(signExtend(relu), nodeScale("headHidden"), nodeScale("headActivation"));
		end
		headOutput.put(value);
	endrule

	rule process8 ( !outputOn );
		let value <- headOutput.get;
		resultR <= value.data;
		outputCnt <= 0;
		outputOn <= True;
	endrule

	rule process9 ( outputOn );
		outputQ.enq(resultR[outputCnt]);
		if ( outputCnt == 56 ) outputOn <= False;
		else outputCnt <= outputCnt + 1;
	endrule

	method Action put(Int#(8) value);
		inputQ.enq(value);
	endmethod

	method ActionValue#(Int#(8)) get;
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule

endpackage
