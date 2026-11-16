-- Watchdog: click "Разрешить"/"Allow" on Chrome's remote-debugging prompt
-- sheet whenever it appears. Run as a launchd agent so osascript's own TCC
-- identity is the responsible process for Accessibility permission.
repeat
	try
		tell application "System Events"
			if exists process "Google Chrome" then
				tell process "Google Chrome"
					repeat with w in windows
						try
							set elems to entire contents of w
							repeat with e in elems
								try
									if class of e is button and (name of e is "Разрешить" or name of e is "Allow") then
										click e
									end if
								end try
							end repeat
						end try
					end repeat
				end tell
			end if
		end tell
	end try
	delay 1.5
end repeat
