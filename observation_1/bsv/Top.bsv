package Top;
import Clocks::*;
import Vector::*;
import FIFO::*;
import Uart::*;
import PLL::*;
import SwayTypes::*;
import SwayBaseline::*;

interface SwayTopIfc;
	(* always_ready *) method Bit#(1) ftdi_rxd;
	(* always_enabled, always_ready, prefix="", result="serial_txd" *)
	method Action ftdi_tx(Bit#(1) ftdi_txd);
	(* always_ready, prefix="", result="led" *) method Bit#(8) led;
endinterface

// Board transport only: not part of kernel-throughput measurement.
// RX: flags(1), tag(2 little-endian), 64 signed16 little-endian values.
// TX: identical framing. flags bit0 starts/resets one sequence.
(* no_default_clock, no_default_reset *)
module mkTop#(Clock clk_25mhz)(SwayTopIfc);
	PLLIfc pll <- mkPllFast(clk_25mhz);
	Reset nullReset = noReset();
	UartIfc uart <- mkUart(217, clocked_by clk_25mhz, reset_by nullReset);
	SyncFIFOIfc#(Bit#(8)) rxQ <- mkSyncFIFO(16, clk_25mhz, nullReset, pll.clk_100mhz);
	SyncFIFOIfc#(Bit#(8)) txQ <- mkSyncFIFO(16, pll.clk_100mhz, pll.rst_100mhz, clk_25mhz);
	SwayBaselineIfc core <- mkSwayBaseline(clocked_by pll.clk_100mhz, reset_by pll.rst_100mhz);
	Reg#(Bit#(8)) rxCnt <- mkReg(0, clocked_by pll.clk_100mhz, reset_by pll.rst_100mhz);
	Reg#(Bit#(8)) lowR <- mkReg(0, clocked_by pll.clk_100mhz, reset_by pll.rst_100mhz);
	Reg#(SwayFrame#(64)) rxFrame <- mkRegU(clocked_by pll.clk_100mhz, reset_by pll.rst_100mhz);
	Reg#(SwayFrame#(64)) txFrame <- mkRegU(clocked_by pll.clk_100mhz, reset_by pll.rst_100mhz);
	Reg#(Bit#(8)) txCnt <- mkReg(0, clocked_by pll.clk_100mhz, reset_by pll.rst_100mhz);
	Reg#(Bool) sendOn <- mkReg(False, clocked_by pll.clk_100mhz, reset_by pll.rst_100mhz);
	rule uartIn;
		let x <- uart.user.get;
		rxQ.enq(x);
	endrule
	rule receive;
		let x = rxQ.first;
		rxQ.deq;
		let nextFrame = rxFrame;
		if ( rxCnt == 0 ) nextFrame.first = unpack(x[0]);
		else if ( rxCnt == 1 ) nextFrame.tag[7:0] = x;
		else if ( rxCnt == 2 ) nextFrame.tag[15:8] = x;
		else if ( rxCnt[0] == 1 ) lowR <= x;
		else begin
			Bit#(6) index = truncate((rxCnt - 4) >> 1);
			nextFrame.data[index] = unpack({x, lowR});
		end
		rxFrame <= nextFrame;
		if ( rxCnt == 130 ) begin
			core.put(nextFrame);
			rxCnt <= 0;
		end else rxCnt <= rxCnt + 1;
	endrule
	rule collect ( !sendOn );
		let x <- core.get;
		txFrame <= x;
		txCnt <= 0;
		sendOn <= True;
	endrule
	rule serialize ( sendOn );
		Bit#(8) x = 0;
		if ( txCnt == 0 ) x = zeroExtend(pack(txFrame.first));
		else if ( txCnt == 1 ) x = txFrame.tag[7:0];
		else if ( txCnt == 2 ) x = txFrame.tag[15:8];
		else begin
			Bit#(6) index = truncate((txCnt - 3) >> 1);
			Bit#(16) wordR = pack(txFrame.data[index]);
			x = txCnt[0] == 1 ? wordR[7:0] : wordR[15:8];
		end
		txQ.enq(x);
		if ( txCnt == 130 ) sendOn <= False;
		else txCnt <= txCnt + 1;
	endrule
	rule uartOut;
		uart.user.send(txQ.first);
		txQ.deq;
	endrule
	method Bit#(1) ftdi_rxd = uart.serial_txd;
	method Action ftdi_tx(Bit#(1) ftdi_txd);
		uart.serial_rx(ftdi_txd);
	endmethod
	method Bit#(8) led = 8'h01;
endmodule
endpackage
