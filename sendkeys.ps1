param([string]$keys, [int]$procId)

Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Win32Focus {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
}
"@

# bring the EVE window to the foreground so SendKeys reaches it;
# restore ONLY if minimized - never touch a maximized window's geometry
$proc = Get-Process -Id $procId -ErrorAction Stop
$hwnd = $proc.MainWindowHandle
if ([Win32Focus]::IsIconic($hwnd)) {
    [Win32Focus]::ShowWindow($hwnd, 9) | Out-Null   # SW_RESTORE
    Start-Sleep -Milliseconds 300
}
[Win32Focus]::SetForegroundWindow($hwnd) | Out-Null
Start-Sleep -Milliseconds 250

[System.Windows.Forms.SendKeys]::SendWait($keys)
Write-Output "sent keys: $keys"
