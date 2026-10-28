PROGRAM PLC_PRG
VAR_INPUT
    Enable : BOOL;
    Target : INT;
END_VAR
VAR_OUTPUT
    Command : INT;
    AtTarget : BOOL;
END_VAR
VAR
    Clamped : INT;
END_VAR
Clamped := LIMIT(0, Target, 100);
IF NOT Enable THEN
    Command := 0;
ELSIF Command < Clamped THEN
    Command := MIN(Command + 5, Clamped);
ELSIF Command > Clamped THEN
    Command := MAX(Command - 5, Clamped);
END_IF;
AtTarget := Command = Clamped;
END_PROGRAM
