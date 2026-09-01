param([int]$x, [int]$y, [int]$delta = -360, [int]$procId = 0)

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Scroller {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    public const uint WHEEL = 0x0800;
}
"@

# EVE only processes wheel input while it has focus: bring it to the foreground
if ($procId -ne 0) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc -and $proc.MainWindowHandle -ne 0) {
        [Scroller]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
        Start-Sleep -Milliseconds 250
    }
}

[Scroller]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 120
[Scroller]::mouse_event([Scroller]::WHEEL, 0, 0, $delta, [UIntPtr]::Zero)
Write-Output "scrolled at ($x, $y) delta=$delta"
