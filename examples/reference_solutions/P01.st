PROGRAM PLC_PRG
VAR_INPUT
    bottleSensor, cleaningConfirmButton, fillingConfirmButton : BOOL;
    cappingConfirmButton, packingConfirmButton, finishedButton : BOOL;
END_VAR
VAR_OUTPUT
    Pump_Motor, Filling_Valve, Capping_Machine, Packing_Machine, Completion_Light : BOOL;
END_VAR
VAR
    phase : INT := 0;
END_VAR
CASE phase OF
    0: IF bottleSensor THEN phase := 1; END_IF;
    1: IF cleaningConfirmButton THEN phase := 2; END_IF;
    2: IF fillingConfirmButton THEN phase := 3; END_IF;
    3: IF cappingConfirmButton THEN phase := 4; END_IF;
    4: IF packingConfirmButton THEN phase := 5; END_IF;
    5: IF finishedButton THEN phase := 0; END_IF;
END_CASE;
Pump_Motor := phase = 1;
Filling_Valve := phase = 2;
Capping_Machine := phase = 3;
Packing_Machine := phase = 4;
Completion_Light := phase = 5;
END_PROGRAM
