PROGRAM PLC_PRG
VAR_INPUT
    StartBtn : BOOL;
    StopBtn : BOOL;
    EStop : BOOL;
END_VAR
VAR_OUTPUT
    Motor : BOOL;
END_VAR
VAR

END_VAR
IF StopBtn OR EStop THEN
    Motor := FALSE;
ELSIF StartBtn THEN
    Motor := TRUE;
END_IF;
END_PROGRAM
