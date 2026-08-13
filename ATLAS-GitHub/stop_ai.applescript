-- Stop ATLAS
-- Opens Terminal and stops the assistant from the folder containing this script.

set scriptFolder to POSIX path of (path to me)
set projectFolder to do shell script "dirname " & quoted form of scriptFolder

tell application "Terminal"
    activate
    do script "cd " & quoted form of projectFolder & " && ./stop.sh"
    delay 3
    set miniaturized of front window to true
end tell
