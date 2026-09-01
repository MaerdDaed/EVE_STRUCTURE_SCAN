param([int]$x1, [int]$y1, [int]$x2, [int]$y2, [int]$procId = 0, [int]$steps = 14)

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Dragger {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    public const uint LEFTDOWN = 0x0002;
    public const uint LEFTUP = 0x0004;
}
"@

if ($procId -ne 0) {
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    if ($proc -and $proc.MainWindowHandle -ne 0) {
        [Dragger]::SetForegroundWindow($proc.MainWindowHandle) | Out-Null
        Start-Sleep -Milliseconds 250
    }
}

[Dragger]::SetCursorPos($x1, $y1) | Out-Null
Start-Sleep -Milliseconds 150
[Dragger]::mouse_event([Dragger]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 80
for ($i = 1; $i -le $steps; $i++) {
    $x = [int]($x1 + ($x2 - $x1) * $i / $steps)
    $y = [int]($y1 + ($y2 - $y1) * $i / $steps)
    [Dragger]::SetCursorPos($x, $y) | Out-Null
    Start-Sleep -Milliseconds 25
}
Start-Sleep -Milliseconds 80
[Dragger]::mouse_event([Dragger]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
Write-Output "dragged ($x1,$y1) -> ($x2,$y2)"
