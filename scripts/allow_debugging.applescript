-- Click "Разрешить"/"Allow" on Chrome's remote-debugging prompt sheet.
-- Run: osascript scripts/allow_debugging.applescript
tell application "System Events"
	tell process "Google Chrome"
		repeat with w in windows
			try
				set elems to entire contents of w
				repeat with e in elems
					try
						if class of e is button and (name of e is "Разрешить" or name of e is "Allow") then
							click e
							return "clicked"
						end if
					end try
				end repeat
			end try
		end repeat
		return "not found"
	end tell
end tell
