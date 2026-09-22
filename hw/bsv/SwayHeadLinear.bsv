package SwayHeadLinear;

import FIFO::*;
import RegFile::*;
import Vector::*;

import SwayTypes::*;
import SwayReset::*;
import SwayLinear::*;
`ifdef SWAY_REALLOCATE
import SwayFoldedLinear::*;
`endif

typedef struct {
	Bit#(4) lane;
	Bit#(6) address;
	Int#(8) value;
	Bool last;
} HeadWrite deriving (Bits);

// Receives one frame as sixteen consecutive Token#(20) values. Token order is
// index 0 through 15, matching the two Mamba blocks. The shared layer-9 engine
// reads the flattened frame in the original token-major order.
module mkSwayHeadLinear(LinearIfc#(20, 20));
	LocalResetIfc localReset <- mkSwayLocalReset;
	FIFO#(Token#(20)) inputQ <- mkFIFO1(reset_by localReset.rst);
	FIFO#(Bit#(1)) freeBankQ <- mkFIFO(reset_by localReset.rst);
	FIFO#(Bit#(1)) readyBankQ <- mkFIFO(reset_by localReset.rst);
	FIFO#(HeadWrite) writeQ <- mkFIFO1(reset_by localReset.rst);
	Vector#(16, RegFile#(Bit#(6), Int#(8))) memoryR <- replicateM(mkRegFileFull);
	Reg#(Bit#(2)) initializeCnt <- mkReg(0, reset_by localReset.rst);
	Reg#(Bit#(1)) writeBankR <- mkReg(0, reset_by localReset.rst);
	Reg#(Bit#(1)) readBankR <- mkReg(0, reset_by localReset.rst);
	Reg#(Bit#(9)) writeCnt <- mkReg(0, reset_by localReset.rst);
	Reg#(Bit#(5)) featureCnt <- mkReg(0, reset_by localReset.rst);
	Reg#(Bool) fillingOn <- mkReg(False, reset_by localReset.rst);
	Reg#(Bool) committedOn <- mkReg(False, reset_by localReset.rst);

	//------------------------------------------------------------------------------------
	// [STAGE 1]
	// Allocate a frame bank and write twenty bytes from each incoming token.
	// Address increments replace token-index multiplication. Only one byte bank
	// receives a write on a cycle; no 320-byte register load is present.
	//------------------------------------------------------------------------------------
	rule initializeBanks ( initializeCnt < 2 );
		freeBankQ.enq(truncate(initializeCnt));
		initializeCnt <= initializeCnt + 1;
	endrule

	rule process1 ( initializeCnt == 2 && !fillingOn );
		writeBankR <= freeBankQ.first;
		freeBankQ.deq;
		writeCnt <= 0;
		featureCnt <= 0;
		fillingOn <= True;
	endrule

	rule process2 ( fillingOn && writeCnt < 320 );
		Int#(8) value = inputQ.first.data[featureCnt];
		Bit#(4) byteBank = truncate(writeCnt);
		Bit#(5) row = truncate(writeCnt >> 4);
		writeQ.enq(HeadWrite { lane: byteBank, address: {writeBankR, row}, value: value, last: writeCnt == 319 });
		if ( featureCnt == 19 ) begin
			inputQ.deq;
			featureCnt <= 0;
		end else begin
			featureCnt <= featureCnt + 1;
		end
		writeCnt <= writeCnt + 1;
	endrule

	// RAM write enables depend only on a registered command and its byte lane.
	// Publish a bank on the following cycle, after its last write has committed.
	rule process3Write;
		let command = writeQ.first;
		writeQ.deq;
		for ( Integer lane = 0; lane < 16; lane = lane + 1 ) begin
			if ( command.lane == fromInteger(lane) ) begin
				memoryR[lane].upd(command.address, command.value);
			end
		end
		if ( command.last ) begin
			committedOn <= True;
		end
	endrule

	rule process4Publish ( committedOn );
		readyBankQ.enq(writeBankR);
		committedOn <= False;
		fillingOn <= False;
	endrule

	//------------------------------------------------------------------------------------
	// [STAGE 2]
	// All sixteen byte banks are read at one row for the existing chunk register.
	// The current frame remains allocated until the engine registers its result.
	//------------------------------------------------------------------------------------
	LinearSourceIfc source = interface LinearSourceIfc;
		method ActionValue#(Bit#(4)) getStart;
			readBankR <= readyBankQ.first;
			readyBankQ.deq;
			return 0;
		endmethod

		method Vector#(16, Int#(8)) readChunk(Bit#(5) chunk);
			Vector#(16, Int#(8)) values = newVector;
			for ( Integer lane = 0; lane < 16; lane = lane + 1 ) begin
				values[lane] = memoryR[lane].sub({readBankR, chunk});
			end
			return values;
		endmethod

		method Action finish if ( initializeCnt == 2 );
			freeBankQ.enq(readBankR);
		endmethod
	endinterface;

`ifdef SWAY_REALLOCATE
	LinearEngineIfc#(20) engine <- mkSwayFoldedLinearEngine(9, 320, 1, source);
`else
	LinearEngineIfc#(20) engine <- mkSwayLinearEngine(9, 320, source);
`endif

	method Action put(Token#(20) value) if ( localReset.ready );
		inputQ.enq(value);
	endmethod

	method ActionValue#(Token#(20)) get if ( localReset.ready );
		let value <- engine.get;
		return value;
	endmethod
endmodule

endpackage
