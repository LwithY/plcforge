PROGRAM PLC_PRG
VAR_INPUT
    Raw : INT;
END_VAR
VAR_OUTPUT
    Eng : REAL;
    Fault : BOOL;
END_VAR
VAR
    Clamped : INT;
END_VAR
Fault := (Raw < 0) OR (Raw > 27648);
Clamped := LIMIT(0, Raw, 27648);
Eng := INT_TO_REAL(Clamped) * 100.0 / 27648.0;
END_PROGRAM
