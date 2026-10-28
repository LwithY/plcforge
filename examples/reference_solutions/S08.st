PROGRAM PLC_PRG
VAR_INPUT
    Remote : BOOL;
    AutoCmd : BOOL;
    ManualCmd : BOOL;
    Permit : BOOL;
    Fault : BOOL;
END_VAR
VAR_OUTPUT
    Run : BOOL;
    Mode : INT;
END_VAR
VAR

END_VAR
IF Remote THEN
    Mode := 1;
    Run := AutoCmd AND Permit AND NOT Fault;
ELSE
    Mode := 0;
    Run := ManualCmd AND Permit AND NOT Fault;
END_IF;
END_PROGRAM
