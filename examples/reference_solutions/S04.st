PROGRAM PLC_PRG
VAR_INPUT
    Sensor : BOOL;
    Reset : BOOL;
END_VAR
VAR_OUTPUT
    Count : INT;
    Done : BOOL;
END_VAR
VAR
    Prev : BOOL;
END_VAR
IF Reset THEN
    Count := 0;
ELSIF Sensor AND NOT Prev AND Count < 3 THEN
    Count := Count + 1;
END_IF;
Prev := Sensor;
Done := Count >= 3;
END_PROGRAM
