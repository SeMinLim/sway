package SwayBaseline;

import Vector::*;
import FIFO::*;
import RegFile::*;

import SwayTypes::*;
import SwayReset::*;
import SwayParameters::*;
import SwayLinear::*;
import SwayHeadLinear::*;
import SwayBlock::*;

// Fixed-weight MARS graph. Each affine/normalization/scan stage has its own
// engine. FIFOs permit adjacent tokens and independent frames to overlap.
module mkSwayBaseline(SwayIfc);
	LocalResetIfc localReset <- mkSwayLocalReset;
	FIFO#(Int#(8)) inputQ <- mkSizedFIFO(32, reset_by localReset.rst);
	FIFO#(Int#(8)) outputQ <- mkSizedFIFO(32, reset_by localReset.rst);
	RegFile#(Bit#(10), Int#(8)) frameMemory <- mkRegFileFull;
	FIFO#(Bit#(1)) freeBankQ <- mkFIFO(reset_by localReset.rst);
	FIFO#(Bit#(1)) readyBankQ <- mkFIFO(reset_by localReset.rst);
	FIFO#(Tuple2#(Bit#(10), Bit#(5))) patchAddressQ <- mkFIFO(reset_by localReset.rst);
	FIFO#(Tuple2#(Int#(8), Bit#(5))) patchByteQ <- mkFIFO(reset_by localReset.rst);
	FIFO#(Token#(20)) patchTokenQ <- mkFIFO1(reset_by localReset.rst);
	Reg#(Bit#(1)) initializeCnt <- mkReg(0, reset_by localReset.rst);
	Reg#(Bool) banksInitialized <- mkReg(False, reset_by localReset.rst);
	Reg#(Bit#(1)) writeBankR <- mkReg(0, reset_by localReset.rst);
	Reg#(Bit#(1)) readBankR <- mkReg(0, reset_by localReset.rst);
	Reg#(Bool) fillOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bit#(9)) inputCnt <- mkReg(0, reset_by localReset.rst);

	LinearIfc#(20, 20) embedding <- mkSwayLinear(0);
	BlockIfc block0 <- mkSwayBlock(0);
	BlockIfc block1 <- mkSwayBlock(1);
	LinearIfc#(20, 20) headHidden <- mkSwayHeadLinear;
	LinearIfc#(20, 57) headOutput <- mkSwayLinear(10);

	Reg#(Bool) patchOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) patchPrepareOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) patchIssueOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bit#(4)) patchCnt <- mkReg(0, reset_by localReset.rst);
	Reg#(Bit#(5)) patchElementCnt <- mkReg(0, reset_by localReset.rst);
	Reg#(Bit#(9)) patchBaseR <- mkRegU;
	Reg#(Vector#(20, Int#(8))) patchR <- mkRegU;
	Reg#(Vector#(57, Int#(8))) resultR <- mkRegU;
	Reg#(Bool) outputOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bit#(6)) outputCnt <- mkReg(0, reset_by localReset.rst);

`ifdef SWAY_PROFILE
	// Simulation-only observations. The root reset matches TbSwayPerf's clock origin.
	// Each boundary owns its frame counter; no functional rule reads these counters.
	Reg#(UInt#(64)) profileCycleCnt <- mkReg(0);
	Reg#(UInt#(32)) profilePatchFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) profileEmbeddingFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) profileBlock0FrameCnt <- mkReg(0);
	Reg#(UInt#(32)) profileBlock1FrameCnt <- mkReg(0);
	Reg#(UInt#(32)) profileHeadFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) profileHeadOutputFrameCnt <- mkReg(0);
	Reg#(UInt#(32)) profileSerializeFrameCnt <- mkReg(0);

	rule profileTick;
		profileCycleCnt <= profileCycleCnt + 1;
	endrule
`endif

	//------------------------------------------------------------------------------------
	// [STAGE 1] Two RAM banks retain complete HWC frames. A bank is reused
	// only after its last patch is retained in a separate token register/FIFO.
	//------------------------------------------------------------------------------------
	rule initializeBanks ( !banksInitialized );
		freeBankQ.enq(initializeCnt);
		initializeCnt <= 1;
		if ( initializeCnt == 1 ) banksInitialized <= True;
	endrule

	rule process1_1 ( banksInitialized && !fillOn );
		writeBankR <= freeBankQ.first;
		freeBankQ.deq;
		inputCnt <= 0;
		fillOn <= True;
	endrule

	rule process1_2 ( fillOn );
		frameMemory.upd({writeBankR, inputCnt}, inputQ.first);
		inputQ.deq;
		if ( inputCnt == 319 ) begin
			readyBankQ.enq(writeBankR);
			fillOn <= False;
		end else inputCnt <= inputCnt + 1;
	endrule

	rule process2 ( !patchOn );
		readBankR <= readyBankQ.first;
		readyBankQ.deq;
		patchCnt <= 0;
		patchElementCnt <= 0;
		patchPrepareOn <= True;
		patchIssueOn <= False;
		patchOn <= True;
	endrule

	// Patch selection and byte-address addition terminate at separate registers.
	rule process3Prepare ( patchOn && patchPrepareOn && !patchIssueOn );
		Vector#(16, Bit#(9)) base = newVector;
		for ( Integer p = 0; p < 16; p = p + 1 ) begin
			base[p] = fromInteger((p / 4) * 80 + (p % 4) * 10);
		end
		patchBaseR <= base[patchCnt];
		patchPrepareOn <= False;
		patchIssueOn <= True;
	endrule

	rule process3_1 ( patchOn && patchIssueOn && !patchPrepareOn );
		Bit#(9) offset = zeroExtend(patchElementCnt) + (patchElementCnt >= 10 ? 30 : 0);
		Bit#(9) address = patchBaseR + offset;
		patchAddressQ.enq(tuple2({readBankR, address}, patchElementCnt));
		if ( patchElementCnt == 19 ) patchIssueOn <= False;
		else patchElementCnt <= patchElementCnt + 1;
	endrule

	rule process3_2;
		let value = patchAddressQ.first;
		patchAddressQ.deq;
		patchByteQ.enq(tuple2(frameMemory.sub(tpl_1(value)), tpl_2(value)));
	endrule

	rule process3_3 ( patchOn && tpl_2(patchByteQ.first) != 19 );
		let value = patchByteQ.first;
		patchByteQ.deq;
		Vector#(20, Int#(8)) patch = patchR;
		patch[tpl_2(value)] = requant(signExtend(tpl_1(value)), nodeScale("input"), nodeScale("patches"));
		patchR <= patch;
	endrule

	rule process3Last ( banksInitialized && patchOn && !patchIssueOn && !patchPrepareOn
		&& tpl_2(patchByteQ.first) == 19 );
		let value = patchByteQ.first;
		patchByteQ.deq;
		Vector#(20, Int#(8)) patch = patchR;
		patch[19] = requant(signExtend(tpl_1(value)), nodeScale("input"), nodeScale("patches"));
		patchTokenQ.enq(Token { index: patchCnt, data: patch });
`ifdef SWAY_PROFILE
		$display("SWAY_PROFILE,patch_ready,%0d,%0d,%0d", profilePatchFrameCnt, patchCnt, profileCycleCnt);
		if ( patchCnt == 15 ) profilePatchFrameCnt <= profilePatchFrameCnt + 1;
`endif
		if ( patchCnt == 15 ) begin
			freeBankQ.enq(readBankR);
			patchOn <= False;
		end else begin
			patchCnt <= patchCnt + 1;
			patchElementCnt <= 0;
			patchPrepareOn <= True;
		end
	endrule

	// This occupied bit cuts embedding readiness out of RAM address/control paths.
	rule process3Embed;
		embedding.put(patchTokenQ.first);
`ifdef SWAY_PROFILE
		$display("SWAY_PROFILE,embedding_in,%0d,%0d,%0d", profileEmbeddingFrameCnt, patchTokenQ.first.index, profileCycleCnt);
		if ( patchTokenQ.first.index == 15 ) profileEmbeddingFrameCnt <= profileEmbeddingFrameCnt + 1;
`endif
		patchTokenQ.deq;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2] Two dedicated Mamba block pipelines.
	//------------------------------------------------------------------------------------
	rule process4;
		let value <- embedding.get;
		block0.put(value);
`ifdef SWAY_PROFILE
		$display("SWAY_PROFILE,block0_in,%0d,%0d,%0d", profileBlock0FrameCnt, value.index, profileCycleCnt);
		if ( value.index == 15 ) profileBlock0FrameCnt <= profileBlock0FrameCnt + 1;
`endif
	endrule

	rule process5;
		let value <- block0.get;
		block1.put(value);
`ifdef SWAY_PROFILE
		$display("SWAY_PROFILE,block1_in,%0d,%0d,%0d", profileBlock1FrameCnt, value.index, profileCycleCnt);
		if ( value.index == 15 ) profileBlock1FrameCnt <= profileBlock1FrameCnt + 1;
`endif
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
		headHidden.put(Token { index: value.index, data: token });
`ifdef SWAY_PROFILE
		$display("SWAY_PROFILE,head_in,%0d,%0d,%0d", profileHeadFrameCnt, value.index, profileCycleCnt);
		if ( value.index == 15 ) profileHeadFrameCnt <= profileHeadFrameCnt + 1;
`endif
	endrule

	rule process7;
		Token#(20) value <- headHidden.get;
		for ( Integer i = 0; i < 20; i = i + 1 ) begin
			Int#(8) relu = value.data[i] < 0 ? 0 : value.data[i];
			value.data[i] = requant(signExtend(relu), nodeScale("headHidden"), nodeScale("headActivation"));
		end
		headOutput.put(value);
`ifdef SWAY_PROFILE
		$display("SWAY_PROFILE,head_output_in,%0d,%0d,%0d", profileHeadOutputFrameCnt, value.index, profileCycleCnt);
		profileHeadOutputFrameCnt <= profileHeadOutputFrameCnt + 1;
`endif
	endrule

	rule process8 ( !outputOn );
		let value <- headOutput.get;
`ifdef SWAY_PROFILE
		$display("SWAY_PROFILE,serialize_in,%0d,%0d,%0d", profileSerializeFrameCnt, value.index, profileCycleCnt);
		profileSerializeFrameCnt <= profileSerializeFrameCnt + 1;
`endif
		resultR <= value.data;
		outputCnt <= 0;
		outputOn <= True;
	endrule

	rule process9 ( outputOn );
		outputQ.enq(resultR[0]);
		Vector#(57, Int#(8)) shifted = newVector;
		for ( Integer i = 0; i < 56; i = i + 1 ) begin
			shifted[i] = resultR[i + 1];
		end
		shifted[56] = 0;
		resultR <= shifted;
		if ( outputCnt == 56 ) outputOn <= False;
		else outputCnt <= outputCnt + 1;
	endrule

	method Action put(Int#(8) value) if ( localReset.ready );
		inputQ.enq(value);
	endmethod

	method ActionValue#(Int#(8)) get if ( localReset.ready );
		let value = outputQ.first;
		outputQ.deq;
		return value;
	endmethod
endmodule

endpackage
