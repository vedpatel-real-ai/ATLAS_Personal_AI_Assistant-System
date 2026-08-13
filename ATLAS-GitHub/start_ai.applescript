-- Start ATLAS
-- Opens Terminal and starts the assistant from the folder containing this script.

set scriptFolder to POSIX path of (path to me)
set projectFolder to do shell script "dirname " & quoted form of scriptFolder

tell application "Terminal"
    activate
    do script "cd " & quoted form of projectFolder & " && ./start.sh && open http://127.0.0.1:8420/; echo ''; echo '--- live logs ---'; tail -f logs/ollama.log logs/assistant.log"
    delay 6
    set miniaturized of front window to true
end tell
