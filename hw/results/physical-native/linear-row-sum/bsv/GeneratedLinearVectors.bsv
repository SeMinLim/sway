// Fixed vectors from the frozen integer reference; no hardware-derived expected values.
package GeneratedLinearVectors;

function Int#(8) inputValue(Bit#(4) token, Integer column);
	Int#(8) result = 0;
	if ( column == 0 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -109;
			4'd3: result = 103;
			default: result = 0;
		endcase
	end
	if ( column == 1 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -36;
			4'd3: result = 120;
			default: result = 0;
		endcase
	end
	if ( column == 2 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 37;
			4'd3: result = -119;
			default: result = 0;
		endcase
	end
	if ( column == 3 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 110;
			4'd3: result = -102;
			default: result = 0;
		endcase
	end
	if ( column == 4 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -73;
			4'd3: result = -85;
			default: result = 0;
		endcase
	end
	if ( column == 5 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 0;
			4'd3: result = -68;
			default: result = 0;
		endcase
	end
	if ( column == 6 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 73;
			4'd3: result = -51;
			default: result = 0;
		endcase
	end
	if ( column == 7 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -110;
			4'd3: result = -34;
			default: result = 0;
		endcase
	end
	if ( column == 8 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -37;
			4'd3: result = -17;
			default: result = 0;
		endcase
	end
	if ( column == 9 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 36;
			4'd3: result = 0;
			default: result = 0;
		endcase
	end
	if ( column == 10 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 109;
			4'd3: result = 17;
			default: result = 0;
		endcase
	end
	if ( column == 11 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -74;
			4'd3: result = 34;
			default: result = 0;
		endcase
	end
	if ( column == 12 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -1;
			4'd3: result = 51;
			default: result = 0;
		endcase
	end
	if ( column == 13 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 72;
			4'd3: result = 68;
			default: result = 0;
		endcase
	end
	if ( column == 14 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -111;
			4'd3: result = 85;
			default: result = 0;
		endcase
	end
	if ( column == 15 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -38;
			4'd3: result = 102;
			default: result = 0;
		endcase
	end
	if ( column == 16 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 35;
			4'd3: result = 119;
			default: result = 0;
		endcase
	end
	if ( column == 17 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 108;
			4'd3: result = -120;
			default: result = 0;
		endcase
	end
	if ( column == 18 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -75;
			4'd3: result = -103;
			default: result = 0;
		endcase
	end
	if ( column == 19 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -2;
			4'd3: result = -86;
			default: result = 0;
		endcase
	end
	if ( column == 20 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 71;
			4'd3: result = -69;
			default: result = 0;
		endcase
	end
	if ( column == 21 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -112;
			4'd3: result = -52;
			default: result = 0;
		endcase
	end
	if ( column == 22 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -39;
			4'd3: result = -35;
			default: result = 0;
		endcase
	end
	if ( column == 23 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 34;
			4'd3: result = -18;
			default: result = 0;
		endcase
	end
	if ( column == 24 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 107;
			4'd3: result = -1;
			default: result = 0;
		endcase
	end
	if ( column == 25 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -76;
			4'd3: result = 16;
			default: result = 0;
		endcase
	end
	if ( column == 26 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -3;
			4'd3: result = 33;
			default: result = 0;
		endcase
	end
	if ( column == 27 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 70;
			4'd3: result = 50;
			default: result = 0;
		endcase
	end
	if ( column == 28 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -113;
			4'd3: result = 67;
			default: result = 0;
		endcase
	end
	if ( column == 29 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -40;
			4'd3: result = 84;
			default: result = 0;
		endcase
	end
	if ( column == 30 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 33;
			4'd3: result = 101;
			default: result = 0;
		endcase
	end
	if ( column == 31 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 106;
			4'd3: result = 118;
			default: result = 0;
		endcase
	end
	if ( column == 32 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -77;
			4'd3: result = -121;
			default: result = 0;
		endcase
	end
	if ( column == 33 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -4;
			4'd3: result = -104;
			default: result = 0;
		endcase
	end
	if ( column == 34 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 69;
			4'd3: result = -87;
			default: result = 0;
		endcase
	end
	if ( column == 35 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -114;
			4'd3: result = -70;
			default: result = 0;
		endcase
	end
	if ( column == 36 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -41;
			4'd3: result = -53;
			default: result = 0;
		endcase
	end
	if ( column == 37 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 32;
			4'd3: result = -36;
			default: result = 0;
		endcase
	end
	if ( column == 38 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 105;
			4'd3: result = -19;
			default: result = 0;
		endcase
	end
	if ( column == 39 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -78;
			4'd3: result = -2;
			default: result = 0;
		endcase
	end
	if ( column == 40 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -5;
			4'd3: result = 15;
			default: result = 0;
		endcase
	end
	if ( column == 41 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 68;
			4'd3: result = 32;
			default: result = 0;
		endcase
	end
	if ( column == 42 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -115;
			4'd3: result = 49;
			default: result = 0;
		endcase
	end
	if ( column == 43 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -42;
			4'd3: result = 66;
			default: result = 0;
		endcase
	end
	if ( column == 44 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 31;
			4'd3: result = 83;
			default: result = 0;
		endcase
	end
	if ( column == 45 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 104;
			4'd3: result = 100;
			default: result = 0;
		endcase
	end
	if ( column == 46 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -79;
			4'd3: result = 117;
			default: result = 0;
		endcase
	end
	if ( column == 47 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -6;
			4'd3: result = -122;
			default: result = 0;
		endcase
	end
	if ( column == 48 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 67;
			4'd3: result = -105;
			default: result = 0;
		endcase
	end
	if ( column == 49 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -116;
			4'd3: result = -88;
			default: result = 0;
		endcase
	end
	if ( column == 50 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -43;
			4'd3: result = -71;
			default: result = 0;
		endcase
	end
	if ( column == 51 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 30;
			4'd3: result = -54;
			default: result = 0;
		endcase
	end
	if ( column == 52 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 103;
			4'd3: result = -37;
			default: result = 0;
		endcase
	end
	if ( column == 53 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -80;
			4'd3: result = -20;
			default: result = 0;
		endcase
	end
	if ( column == 54 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -7;
			4'd3: result = -3;
			default: result = 0;
		endcase
	end
	if ( column == 55 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 66;
			4'd3: result = 14;
			default: result = 0;
		endcase
	end
	if ( column == 56 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -117;
			4'd3: result = 31;
			default: result = 0;
		endcase
	end
	if ( column == 57 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -44;
			4'd3: result = 48;
			default: result = 0;
		endcase
	end
	if ( column == 58 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 29;
			4'd3: result = 65;
			default: result = 0;
		endcase
	end
	if ( column == 59 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 102;
			4'd3: result = 82;
			default: result = 0;
		endcase
	end
	if ( column == 60 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -81;
			4'd3: result = 99;
			default: result = 0;
		endcase
	end
	if ( column == 61 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -8;
			4'd3: result = 116;
			default: result = 0;
		endcase
	end
	if ( column == 62 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 65;
			4'd3: result = -123;
			default: result = 0;
		endcase
	end
	if ( column == 63 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -118;
			4'd3: result = -106;
			default: result = 0;
		endcase
	end
	if ( column == 64 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -45;
			4'd3: result = -89;
			default: result = 0;
		endcase
	end
	if ( column == 65 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 28;
			4'd3: result = -72;
			default: result = 0;
		endcase
	end
	if ( column == 66 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 101;
			4'd3: result = -55;
			default: result = 0;
		endcase
	end
	if ( column == 67 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -82;
			4'd3: result = -38;
			default: result = 0;
		endcase
	end
	if ( column == 68 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -9;
			4'd3: result = -21;
			default: result = 0;
		endcase
	end
	if ( column == 69 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 64;
			4'd3: result = -4;
			default: result = 0;
		endcase
	end
	if ( column == 70 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -119;
			4'd3: result = 13;
			default: result = 0;
		endcase
	end
	if ( column == 71 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -46;
			4'd3: result = 30;
			default: result = 0;
		endcase
	end
	if ( column == 72 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 27;
			4'd3: result = 47;
			default: result = 0;
		endcase
	end
	if ( column == 73 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 100;
			4'd3: result = 64;
			default: result = 0;
		endcase
	end
	if ( column == 74 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -83;
			4'd3: result = 81;
			default: result = 0;
		endcase
	end
	if ( column == 75 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -10;
			4'd3: result = 98;
			default: result = 0;
		endcase
	end
	if ( column == 76 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 63;
			4'd3: result = 115;
			default: result = 0;
		endcase
	end
	if ( column == 77 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -120;
			4'd3: result = -124;
			default: result = 0;
		endcase
	end
	if ( column == 78 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -47;
			4'd3: result = -107;
			default: result = 0;
		endcase
	end
	if ( column == 79 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 26;
			4'd3: result = -90;
			default: result = 0;
		endcase
	end
	if ( column == 80 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 99;
			4'd3: result = -73;
			default: result = 0;
		endcase
	end
	if ( column == 81 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -84;
			4'd3: result = -56;
			default: result = 0;
		endcase
	end
	if ( column == 82 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -11;
			4'd3: result = -39;
			default: result = 0;
		endcase
	end
	if ( column == 83 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 62;
			4'd3: result = -22;
			default: result = 0;
		endcase
	end
	if ( column == 84 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -121;
			4'd3: result = -5;
			default: result = 0;
		endcase
	end
	if ( column == 85 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -48;
			4'd3: result = 12;
			default: result = 0;
		endcase
	end
	if ( column == 86 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 25;
			4'd3: result = 29;
			default: result = 0;
		endcase
	end
	if ( column == 87 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 98;
			4'd3: result = 46;
			default: result = 0;
		endcase
	end
	if ( column == 88 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -85;
			4'd3: result = 63;
			default: result = 0;
		endcase
	end
	if ( column == 89 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -12;
			4'd3: result = 80;
			default: result = 0;
		endcase
	end
	if ( column == 90 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 61;
			4'd3: result = 97;
			default: result = 0;
		endcase
	end
	if ( column == 91 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -122;
			4'd3: result = 114;
			default: result = 0;
		endcase
	end
	if ( column == 92 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -49;
			4'd3: result = -125;
			default: result = 0;
		endcase
	end
	if ( column == 93 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 24;
			4'd3: result = -108;
			default: result = 0;
		endcase
	end
	if ( column == 94 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 97;
			4'd3: result = -91;
			default: result = 0;
		endcase
	end
	if ( column == 95 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -86;
			4'd3: result = -74;
			default: result = 0;
		endcase
	end
	if ( column == 96 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -13;
			4'd3: result = -57;
			default: result = 0;
		endcase
	end
	if ( column == 97 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 60;
			4'd3: result = -40;
			default: result = 0;
		endcase
	end
	if ( column == 98 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -123;
			4'd3: result = -23;
			default: result = 0;
		endcase
	end
	if ( column == 99 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -50;
			4'd3: result = -6;
			default: result = 0;
		endcase
	end
	if ( column == 100 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 23;
			4'd3: result = 11;
			default: result = 0;
		endcase
	end
	if ( column == 101 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 96;
			4'd3: result = 28;
			default: result = 0;
		endcase
	end
	if ( column == 102 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -87;
			4'd3: result = 45;
			default: result = 0;
		endcase
	end
	if ( column == 103 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -14;
			4'd3: result = 62;
			default: result = 0;
		endcase
	end
	if ( column == 104 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 59;
			4'd3: result = 79;
			default: result = 0;
		endcase
	end
	if ( column == 105 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -124;
			4'd3: result = 96;
			default: result = 0;
		endcase
	end
	if ( column == 106 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -51;
			4'd3: result = 113;
			default: result = 0;
		endcase
	end
	if ( column == 107 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 22;
			4'd3: result = -126;
			default: result = 0;
		endcase
	end
	if ( column == 108 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 95;
			4'd3: result = -109;
			default: result = 0;
		endcase
	end
	if ( column == 109 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -88;
			4'd3: result = -92;
			default: result = 0;
		endcase
	end
	if ( column == 110 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -15;
			4'd3: result = -75;
			default: result = 0;
		endcase
	end
	if ( column == 111 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 58;
			4'd3: result = -58;
			default: result = 0;
		endcase
	end
	if ( column == 112 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -125;
			4'd3: result = -41;
			default: result = 0;
		endcase
	end
	if ( column == 113 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -52;
			4'd3: result = -24;
			default: result = 0;
		endcase
	end
	if ( column == 114 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 21;
			4'd3: result = -7;
			default: result = 0;
		endcase
	end
	if ( column == 115 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 94;
			4'd3: result = 10;
			default: result = 0;
		endcase
	end
	if ( column == 116 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -89;
			4'd3: result = 27;
			default: result = 0;
		endcase
	end
	if ( column == 117 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -16;
			4'd3: result = 44;
			default: result = 0;
		endcase
	end
	if ( column == 118 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 57;
			4'd3: result = 61;
			default: result = 0;
		endcase
	end
	if ( column == 119 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -126;
			4'd3: result = 78;
			default: result = 0;
		endcase
	end
	if ( column == 120 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -53;
			4'd3: result = 95;
			default: result = 0;
		endcase
	end
	if ( column == 121 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 20;
			4'd3: result = 112;
			default: result = 0;
		endcase
	end
	if ( column == 122 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 93;
			4'd3: result = -127;
			default: result = 0;
		endcase
	end
	if ( column == 123 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -90;
			4'd3: result = -110;
			default: result = 0;
		endcase
	end
	if ( column == 124 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -17;
			4'd3: result = -93;
			default: result = 0;
		endcase
	end
	if ( column == 125 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 56;
			4'd3: result = -76;
			default: result = 0;
		endcase
	end
	if ( column == 126 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -127;
			4'd3: result = -59;
			default: result = 0;
		endcase
	end
	if ( column == 127 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -54;
			4'd3: result = -42;
			default: result = 0;
		endcase
	end
	if ( column == 128 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 19;
			4'd3: result = -25;
			default: result = 0;
		endcase
	end
	if ( column == 129 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 92;
			4'd3: result = -8;
			default: result = 0;
		endcase
	end
	if ( column == 130 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -91;
			4'd3: result = 9;
			default: result = 0;
		endcase
	end
	if ( column == 131 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -18;
			4'd3: result = 26;
			default: result = 0;
		endcase
	end
	if ( column == 132 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 55;
			4'd3: result = 43;
			default: result = 0;
		endcase
	end
	if ( column == 133 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -128;
			4'd3: result = 60;
			default: result = 0;
		endcase
	end
	if ( column == 134 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -55;
			4'd3: result = 77;
			default: result = 0;
		endcase
	end
	if ( column == 135 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 18;
			4'd3: result = 94;
			default: result = 0;
		endcase
	end
	if ( column == 136 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 91;
			4'd3: result = 111;
			default: result = 0;
		endcase
	end
	if ( column == 137 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -92;
			4'd3: result = -128;
			default: result = 0;
		endcase
	end
	if ( column == 138 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -19;
			4'd3: result = -111;
			default: result = 0;
		endcase
	end
	if ( column == 139 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 54;
			4'd3: result = -94;
			default: result = 0;
		endcase
	end
	if ( column == 140 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 127;
			4'd3: result = -77;
			default: result = 0;
		endcase
	end
	if ( column == 141 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -56;
			4'd3: result = -60;
			default: result = 0;
		endcase
	end
	if ( column == 142 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 17;
			4'd3: result = -43;
			default: result = 0;
		endcase
	end
	if ( column == 143 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 90;
			4'd3: result = -26;
			default: result = 0;
		endcase
	end
	if ( column == 144 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -93;
			4'd3: result = -9;
			default: result = 0;
		endcase
	end
	if ( column == 145 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -20;
			4'd3: result = 8;
			default: result = 0;
		endcase
	end
	if ( column == 146 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 53;
			4'd3: result = 25;
			default: result = 0;
		endcase
	end
	if ( column == 147 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 126;
			4'd3: result = 42;
			default: result = 0;
		endcase
	end
	if ( column == 148 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -57;
			4'd3: result = 59;
			default: result = 0;
		endcase
	end
	if ( column == 149 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 16;
			4'd3: result = 76;
			default: result = 0;
		endcase
	end
	if ( column == 150 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 89;
			4'd3: result = 93;
			default: result = 0;
		endcase
	end
	if ( column == 151 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -94;
			4'd3: result = 110;
			default: result = 0;
		endcase
	end
	if ( column == 152 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -21;
			4'd3: result = 127;
			default: result = 0;
		endcase
	end
	if ( column == 153 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 52;
			4'd3: result = -112;
			default: result = 0;
		endcase
	end
	if ( column == 154 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 125;
			4'd3: result = -95;
			default: result = 0;
		endcase
	end
	if ( column == 155 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -58;
			4'd3: result = -78;
			default: result = 0;
		endcase
	end
	if ( column == 156 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 15;
			4'd3: result = -61;
			default: result = 0;
		endcase
	end
	if ( column == 157 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 88;
			4'd3: result = -44;
			default: result = 0;
		endcase
	end
	if ( column == 158 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -95;
			4'd3: result = -27;
			default: result = 0;
		endcase
	end
	if ( column == 159 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -22;
			4'd3: result = -10;
			default: result = 0;
		endcase
	end
	if ( column == 160 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 51;
			4'd3: result = 7;
			default: result = 0;
		endcase
	end
	if ( column == 161 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 124;
			4'd3: result = 24;
			default: result = 0;
		endcase
	end
	if ( column == 162 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -59;
			4'd3: result = 41;
			default: result = 0;
		endcase
	end
	if ( column == 163 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 14;
			4'd3: result = 58;
			default: result = 0;
		endcase
	end
	if ( column == 164 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 87;
			4'd3: result = 75;
			default: result = 0;
		endcase
	end
	if ( column == 165 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -96;
			4'd3: result = 92;
			default: result = 0;
		endcase
	end
	if ( column == 166 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -23;
			4'd3: result = 109;
			default: result = 0;
		endcase
	end
	if ( column == 167 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 50;
			4'd3: result = 126;
			default: result = 0;
		endcase
	end
	if ( column == 168 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 123;
			4'd3: result = -113;
			default: result = 0;
		endcase
	end
	if ( column == 169 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -60;
			4'd3: result = -96;
			default: result = 0;
		endcase
	end
	if ( column == 170 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 13;
			4'd3: result = -79;
			default: result = 0;
		endcase
	end
	if ( column == 171 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 86;
			4'd3: result = -62;
			default: result = 0;
		endcase
	end
	if ( column == 172 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -97;
			4'd3: result = -45;
			default: result = 0;
		endcase
	end
	if ( column == 173 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -24;
			4'd3: result = -28;
			default: result = 0;
		endcase
	end
	if ( column == 174 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 49;
			4'd3: result = -11;
			default: result = 0;
		endcase
	end
	if ( column == 175 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 122;
			4'd3: result = 6;
			default: result = 0;
		endcase
	end
	if ( column == 176 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -61;
			4'd3: result = 23;
			default: result = 0;
		endcase
	end
	if ( column == 177 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 12;
			4'd3: result = 40;
			default: result = 0;
		endcase
	end
	if ( column == 178 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 85;
			4'd3: result = 57;
			default: result = 0;
		endcase
	end
	if ( column == 179 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -98;
			4'd3: result = 74;
			default: result = 0;
		endcase
	end
	if ( column == 180 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -25;
			4'd3: result = 91;
			default: result = 0;
		endcase
	end
	if ( column == 181 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 48;
			4'd3: result = 108;
			default: result = 0;
		endcase
	end
	if ( column == 182 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 121;
			4'd3: result = 125;
			default: result = 0;
		endcase
	end
	if ( column == 183 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -62;
			4'd3: result = -114;
			default: result = 0;
		endcase
	end
	if ( column == 184 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 11;
			4'd3: result = -97;
			default: result = 0;
		endcase
	end
	if ( column == 185 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 84;
			4'd3: result = -80;
			default: result = 0;
		endcase
	end
	if ( column == 186 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -99;
			4'd3: result = -63;
			default: result = 0;
		endcase
	end
	if ( column == 187 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -26;
			4'd3: result = -46;
			default: result = 0;
		endcase
	end
	if ( column == 188 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 47;
			4'd3: result = -29;
			default: result = 0;
		endcase
	end
	if ( column == 189 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 120;
			4'd3: result = -12;
			default: result = 0;
		endcase
	end
	if ( column == 190 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -63;
			4'd3: result = 5;
			default: result = 0;
		endcase
	end
	if ( column == 191 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 10;
			4'd3: result = 22;
			default: result = 0;
		endcase
	end
	if ( column == 192 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 83;
			4'd3: result = 39;
			default: result = 0;
		endcase
	end
	if ( column == 193 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -100;
			4'd3: result = 56;
			default: result = 0;
		endcase
	end
	if ( column == 194 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -27;
			4'd3: result = 73;
			default: result = 0;
		endcase
	end
	if ( column == 195 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 46;
			4'd3: result = 90;
			default: result = 0;
		endcase
	end
	if ( column == 196 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 119;
			4'd3: result = 107;
			default: result = 0;
		endcase
	end
	if ( column == 197 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -64;
			4'd3: result = 124;
			default: result = 0;
		endcase
	end
	if ( column == 198 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 9;
			4'd3: result = -115;
			default: result = 0;
		endcase
	end
	if ( column == 199 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 82;
			4'd3: result = -98;
			default: result = 0;
		endcase
	end
	if ( column == 200 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -101;
			4'd3: result = -81;
			default: result = 0;
		endcase
	end
	if ( column == 201 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -28;
			4'd3: result = -64;
			default: result = 0;
		endcase
	end
	if ( column == 202 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 45;
			4'd3: result = -47;
			default: result = 0;
		endcase
	end
	if ( column == 203 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 118;
			4'd3: result = -30;
			default: result = 0;
		endcase
	end
	if ( column == 204 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -65;
			4'd3: result = -13;
			default: result = 0;
		endcase
	end
	if ( column == 205 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 8;
			4'd3: result = 4;
			default: result = 0;
		endcase
	end
	if ( column == 206 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 81;
			4'd3: result = 21;
			default: result = 0;
		endcase
	end
	if ( column == 207 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -102;
			4'd3: result = 38;
			default: result = 0;
		endcase
	end
	if ( column == 208 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -29;
			4'd3: result = 55;
			default: result = 0;
		endcase
	end
	if ( column == 209 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 44;
			4'd3: result = 72;
			default: result = 0;
		endcase
	end
	if ( column == 210 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 117;
			4'd3: result = 89;
			default: result = 0;
		endcase
	end
	if ( column == 211 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -66;
			4'd3: result = 106;
			default: result = 0;
		endcase
	end
	if ( column == 212 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 7;
			4'd3: result = 123;
			default: result = 0;
		endcase
	end
	if ( column == 213 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 80;
			4'd3: result = -116;
			default: result = 0;
		endcase
	end
	if ( column == 214 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -103;
			4'd3: result = -99;
			default: result = 0;
		endcase
	end
	if ( column == 215 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -30;
			4'd3: result = -82;
			default: result = 0;
		endcase
	end
	if ( column == 216 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 43;
			4'd3: result = -65;
			default: result = 0;
		endcase
	end
	if ( column == 217 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 116;
			4'd3: result = -48;
			default: result = 0;
		endcase
	end
	if ( column == 218 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -67;
			4'd3: result = -31;
			default: result = 0;
		endcase
	end
	if ( column == 219 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 6;
			4'd3: result = -14;
			default: result = 0;
		endcase
	end
	if ( column == 220 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 79;
			4'd3: result = 3;
			default: result = 0;
		endcase
	end
	if ( column == 221 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -104;
			4'd3: result = 20;
			default: result = 0;
		endcase
	end
	if ( column == 222 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -31;
			4'd3: result = 37;
			default: result = 0;
		endcase
	end
	if ( column == 223 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 42;
			4'd3: result = 54;
			default: result = 0;
		endcase
	end
	if ( column == 224 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 115;
			4'd3: result = 71;
			default: result = 0;
		endcase
	end
	if ( column == 225 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -68;
			4'd3: result = 88;
			default: result = 0;
		endcase
	end
	if ( column == 226 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 5;
			4'd3: result = 105;
			default: result = 0;
		endcase
	end
	if ( column == 227 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 78;
			4'd3: result = 122;
			default: result = 0;
		endcase
	end
	if ( column == 228 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -105;
			4'd3: result = -117;
			default: result = 0;
		endcase
	end
	if ( column == 229 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -32;
			4'd3: result = -100;
			default: result = 0;
		endcase
	end
	if ( column == 230 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 41;
			4'd3: result = -83;
			default: result = 0;
		endcase
	end
	if ( column == 231 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 114;
			4'd3: result = -66;
			default: result = 0;
		endcase
	end
	if ( column == 232 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -69;
			4'd3: result = -49;
			default: result = 0;
		endcase
	end
	if ( column == 233 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 4;
			4'd3: result = -32;
			default: result = 0;
		endcase
	end
	if ( column == 234 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 77;
			4'd3: result = -15;
			default: result = 0;
		endcase
	end
	if ( column == 235 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -106;
			4'd3: result = 2;
			default: result = 0;
		endcase
	end
	if ( column == 236 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -33;
			4'd3: result = 19;
			default: result = 0;
		endcase
	end
	if ( column == 237 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 40;
			4'd3: result = 36;
			default: result = 0;
		endcase
	end
	if ( column == 238 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 113;
			4'd3: result = 53;
			default: result = 0;
		endcase
	end
	if ( column == 239 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -70;
			4'd3: result = 70;
			default: result = 0;
		endcase
	end
	if ( column == 240 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 3;
			4'd3: result = 87;
			default: result = 0;
		endcase
	end
	if ( column == 241 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 76;
			4'd3: result = 104;
			default: result = 0;
		endcase
	end
	if ( column == 242 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -107;
			4'd3: result = 121;
			default: result = 0;
		endcase
	end
	if ( column == 243 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -34;
			4'd3: result = -118;
			default: result = 0;
		endcase
	end
	if ( column == 244 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 39;
			4'd3: result = -101;
			default: result = 0;
		endcase
	end
	if ( column == 245 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 112;
			4'd3: result = -84;
			default: result = 0;
		endcase
	end
	if ( column == 246 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -71;
			4'd3: result = -67;
			default: result = 0;
		endcase
	end
	if ( column == 247 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 2;
			4'd3: result = -50;
			default: result = 0;
		endcase
	end
	if ( column == 248 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 75;
			4'd3: result = -33;
			default: result = 0;
		endcase
	end
	if ( column == 249 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -108;
			4'd3: result = -16;
			default: result = 0;
		endcase
	end
	if ( column == 250 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -35;
			4'd3: result = 1;
			default: result = 0;
		endcase
	end
	if ( column == 251 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 38;
			4'd3: result = 18;
			default: result = 0;
		endcase
	end
	if ( column == 252 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 111;
			4'd3: result = 35;
			default: result = 0;
		endcase
	end
	if ( column == 253 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -72;
			4'd3: result = 52;
			default: result = 0;
		endcase
	end
	if ( column == 254 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 1;
			4'd3: result = 69;
			default: result = 0;
		endcase
	end
	if ( column == 255 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 74;
			4'd3: result = 86;
			default: result = 0;
		endcase
	end
	if ( column == 256 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -109;
			4'd3: result = 103;
			default: result = 0;
		endcase
	end
	if ( column == 257 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -36;
			4'd3: result = 120;
			default: result = 0;
		endcase
	end
	if ( column == 258 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 37;
			4'd3: result = -119;
			default: result = 0;
		endcase
	end
	if ( column == 259 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 110;
			4'd3: result = -102;
			default: result = 0;
		endcase
	end
	if ( column == 260 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -73;
			4'd3: result = -85;
			default: result = 0;
		endcase
	end
	if ( column == 261 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 0;
			4'd3: result = -68;
			default: result = 0;
		endcase
	end
	if ( column == 262 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 73;
			4'd3: result = -51;
			default: result = 0;
		endcase
	end
	if ( column == 263 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -110;
			4'd3: result = -34;
			default: result = 0;
		endcase
	end
	if ( column == 264 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -37;
			4'd3: result = -17;
			default: result = 0;
		endcase
	end
	if ( column == 265 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 36;
			4'd3: result = 0;
			default: result = 0;
		endcase
	end
	if ( column == 266 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 109;
			4'd3: result = 17;
			default: result = 0;
		endcase
	end
	if ( column == 267 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -74;
			4'd3: result = 34;
			default: result = 0;
		endcase
	end
	if ( column == 268 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -1;
			4'd3: result = 51;
			default: result = 0;
		endcase
	end
	if ( column == 269 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 72;
			4'd3: result = 68;
			default: result = 0;
		endcase
	end
	if ( column == 270 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -111;
			4'd3: result = 85;
			default: result = 0;
		endcase
	end
	if ( column == 271 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -38;
			4'd3: result = 102;
			default: result = 0;
		endcase
	end
	if ( column == 272 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 35;
			4'd3: result = 119;
			default: result = 0;
		endcase
	end
	if ( column == 273 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 108;
			4'd3: result = -120;
			default: result = 0;
		endcase
	end
	if ( column == 274 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -75;
			4'd3: result = -103;
			default: result = 0;
		endcase
	end
	if ( column == 275 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -2;
			4'd3: result = -86;
			default: result = 0;
		endcase
	end
	if ( column == 276 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 71;
			4'd3: result = -69;
			default: result = 0;
		endcase
	end
	if ( column == 277 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -112;
			4'd3: result = -52;
			default: result = 0;
		endcase
	end
	if ( column == 278 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -39;
			4'd3: result = -35;
			default: result = 0;
		endcase
	end
	if ( column == 279 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 34;
			4'd3: result = -18;
			default: result = 0;
		endcase
	end
	if ( column == 280 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 107;
			4'd3: result = -1;
			default: result = 0;
		endcase
	end
	if ( column == 281 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -76;
			4'd3: result = 16;
			default: result = 0;
		endcase
	end
	if ( column == 282 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -3;
			4'd3: result = 33;
			default: result = 0;
		endcase
	end
	if ( column == 283 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 70;
			4'd3: result = 50;
			default: result = 0;
		endcase
	end
	if ( column == 284 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -113;
			4'd3: result = 67;
			default: result = 0;
		endcase
	end
	if ( column == 285 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -40;
			4'd3: result = 84;
			default: result = 0;
		endcase
	end
	if ( column == 286 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 33;
			4'd3: result = 101;
			default: result = 0;
		endcase
	end
	if ( column == 287 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 106;
			4'd3: result = 118;
			default: result = 0;
		endcase
	end
	if ( column == 288 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -77;
			4'd3: result = -121;
			default: result = 0;
		endcase
	end
	if ( column == 289 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -4;
			4'd3: result = -104;
			default: result = 0;
		endcase
	end
	if ( column == 290 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 69;
			4'd3: result = -87;
			default: result = 0;
		endcase
	end
	if ( column == 291 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -114;
			4'd3: result = -70;
			default: result = 0;
		endcase
	end
	if ( column == 292 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -41;
			4'd3: result = -53;
			default: result = 0;
		endcase
	end
	if ( column == 293 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 32;
			4'd3: result = -36;
			default: result = 0;
		endcase
	end
	if ( column == 294 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 105;
			4'd3: result = -19;
			default: result = 0;
		endcase
	end
	if ( column == 295 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -78;
			4'd3: result = -2;
			default: result = 0;
		endcase
	end
	if ( column == 296 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -5;
			4'd3: result = 15;
			default: result = 0;
		endcase
	end
	if ( column == 297 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 68;
			4'd3: result = 32;
			default: result = 0;
		endcase
	end
	if ( column == 298 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -115;
			4'd3: result = 49;
			default: result = 0;
		endcase
	end
	if ( column == 299 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -42;
			4'd3: result = 66;
			default: result = 0;
		endcase
	end
	if ( column == 300 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 31;
			4'd3: result = 83;
			default: result = 0;
		endcase
	end
	if ( column == 301 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 104;
			4'd3: result = 100;
			default: result = 0;
		endcase
	end
	if ( column == 302 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -79;
			4'd3: result = 117;
			default: result = 0;
		endcase
	end
	if ( column == 303 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -6;
			4'd3: result = -122;
			default: result = 0;
		endcase
	end
	if ( column == 304 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 67;
			4'd3: result = -105;
			default: result = 0;
		endcase
	end
	if ( column == 305 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -116;
			4'd3: result = -88;
			default: result = 0;
		endcase
	end
	if ( column == 306 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -43;
			4'd3: result = -71;
			default: result = 0;
		endcase
	end
	if ( column == 307 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 30;
			4'd3: result = -54;
			default: result = 0;
		endcase
	end
	if ( column == 308 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 103;
			4'd3: result = -37;
			default: result = 0;
		endcase
	end
	if ( column == 309 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -80;
			4'd3: result = -20;
			default: result = 0;
		endcase
	end
	if ( column == 310 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -7;
			4'd3: result = -3;
			default: result = 0;
		endcase
	end
	if ( column == 311 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 66;
			4'd3: result = 14;
			default: result = 0;
		endcase
	end
	if ( column == 312 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -117;
			4'd3: result = 31;
			default: result = 0;
		endcase
	end
	if ( column == 313 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -44;
			4'd3: result = 48;
			default: result = 0;
		endcase
	end
	if ( column == 314 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 29;
			4'd3: result = 65;
			default: result = 0;
		endcase
	end
	if ( column == 315 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = 102;
			4'd3: result = 82;
			default: result = 0;
		endcase
	end
	if ( column == 316 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = -81;
			4'd3: result = 99;
			default: result = 0;
		endcase
	end
	if ( column == 317 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -8;
			4'd3: result = 116;
			default: result = 0;
		endcase
	end
	if ( column == 318 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = -128;
			4'd2: result = 65;
			4'd3: result = -123;
			default: result = 0;
		endcase
	end
	if ( column == 319 ) begin
		case ( token )
			4'd0: result = 0;
			4'd1: result = 127;
			4'd2: result = -118;
			4'd3: result = -106;
			default: result = 0;
		endcase
	end
	return result;
endfunction

function Int#(8) expectedValue(Bit#(4) token, Integer column);
	Int#(8) result = 0;
	if ( column == 0 ) begin
		case ( token )
			4'd0: result = 1;
			4'd1: result = -12;
			4'd2: result = 13;
			4'd3: result = -40;
			default: result = 0;
		endcase
	end
	if ( column == 1 ) begin
		case ( token )
			4'd0: result = 5;
			4'd1: result = 127;
			4'd2: result = 2;
			4'd3: result = 9;
			default: result = 0;
		endcase
	end
	if ( column == 2 ) begin
		case ( token )
			4'd0: result = 4;
			4'd1: result = 127;
			4'd2: result = -8;
			4'd3: result = 9;
			default: result = 0;
		endcase
	end
	if ( column == 3 ) begin
		case ( token )
			4'd0: result = 5;
			4'd1: result = 35;
			4'd2: result = 6;
			4'd3: result = 46;
			default: result = 0;
		endcase
	end
	if ( column == 4 ) begin
		case ( token )
			4'd0: result = 5;
			4'd1: result = -34;
			4'd2: result = 16;
			4'd3: result = 13;
			default: result = 0;
		endcase
	end
	if ( column == 5 ) begin
		case ( token )
			4'd0: result = -3;
			4'd1: result = -128;
			4'd2: result = 19;
			4'd3: result = 1;
			default: result = 0;
		endcase
	end
	if ( column == 6 ) begin
		case ( token )
			4'd0: result = 2;
			4'd1: result = 127;
			4'd2: result = -2;
			4'd3: result = -41;
			default: result = 0;
		endcase
	end
	if ( column == 7 ) begin
		case ( token )
			4'd0: result = 3;
			4'd1: result = 96;
			4'd2: result = -2;
			4'd3: result = -6;
			default: result = 0;
		endcase
	end
	if ( column == 8 ) begin
		case ( token )
			4'd0: result = 1;
			4'd1: result = -44;
			4'd2: result = 8;
			4'd3: result = -49;
			default: result = 0;
		endcase
	end
	if ( column == 9 ) begin
		case ( token )
			4'd0: result = 4;
			4'd1: result = -37;
			4'd2: result = -12;
			4'd3: result = -19;
			default: result = 0;
		endcase
	end
	if ( column == 10 ) begin
		case ( token )
			4'd0: result = 4;
			4'd1: result = 127;
			4'd2: result = -5;
			4'd3: result = 8;
			default: result = 0;
		endcase
	end
	if ( column == 11 ) begin
		case ( token )
			4'd0: result = 2;
			4'd1: result = -52;
			4'd2: result = 0;
			4'd3: result = -17;
			default: result = 0;
		endcase
	end
	if ( column == 12 ) begin
		case ( token )
			4'd0: result = -3;
			4'd1: result = -128;
			4'd2: result = -15;
			4'd3: result = -14;
			default: result = 0;
		endcase
	end
	if ( column == 13 ) begin
		case ( token )
			4'd0: result = 3;
			4'd1: result = 127;
			4'd2: result = -9;
			4'd3: result = -19;
			default: result = 0;
		endcase
	end
	if ( column == 14 ) begin
		case ( token )
			4'd0: result = -5;
			4'd1: result = -128;
			4'd2: result = -24;
			4'd3: result = 7;
			default: result = 0;
		endcase
	end
	if ( column == 15 ) begin
		case ( token )
			4'd0: result = -3;
			4'd1: result = -128;
			4'd2: result = 34;
			4'd3: result = 5;
			default: result = 0;
		endcase
	end
	if ( column == 16 ) begin
		case ( token )
			4'd0: result = -3;
			4'd1: result = -86;
			4'd2: result = 2;
			4'd3: result = -53;
			default: result = 0;
		endcase
	end
	if ( column == 17 ) begin
		case ( token )
			4'd0: result = 3;
			4'd1: result = 127;
			4'd2: result = 3;
			4'd3: result = 15;
			default: result = 0;
		endcase
	end
	if ( column == 18 ) begin
		case ( token )
			4'd0: result = 2;
			4'd1: result = 127;
			4'd2: result = -13;
			4'd3: result = 25;
			default: result = 0;
		endcase
	end
	if ( column == 19 ) begin
		case ( token )
			4'd0: result = 3;
			4'd1: result = 127;
			4'd2: result = 3;
			4'd3: result = 39;
			default: result = 0;
		endcase
	end
	return result;
endfunction

endpackage
