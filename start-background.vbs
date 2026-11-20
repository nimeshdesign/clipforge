' ClipForge in the background: no window. Double-click to start, run stop.bat to stop.
' Open http://127.0.0.1:8800 for the UI. Errors are written to data\server.log.
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = dir
' 0 = hidden window, False = don't wait
sh.Run """" & dir & "\start.bat"" /autostart", 0, False
