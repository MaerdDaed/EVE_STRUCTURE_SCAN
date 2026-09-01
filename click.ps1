param([int]$x, [int]$y)

Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Clicker {
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    public const uint LEFTDOWN = 0x0002;
    public const uint LEFTUP = 0x0004;
}
"@

[Clicker]::SetCursorPos($x, $y) | Out-Null
Start-Sleep -Milliseconds 120
[Clicker]::mouse_event([Clicker]::LEFTDOWN, 0, 0, 0, [UIntPtr]::Zero)
Start-Sleep -Milliseconds 60
[Clicker]::mouse_event([Clicker]::LEFTUP, 0, 0, 0, [UIntPtr]::Zero)
Write-Output "clicked ($x, $y)"
