# RideShare — run locally

1. Install Flask (one time):
   pip install flask

2. Start the server:
   python app.py

3. Open in your browser:
   http://127.0.0.1:5000

To open RideShare on another device connected to the same Wi-Fi:

1. On Windows, run `ipconfig` and find the host computer's IPv4 Address
   (usually under the active Wi-Fi adapter).
2. On the phone or other device, open `https://<IPv4-address>:5000`, for
   example `http://192.168.1.25:5000`.
   Accept the browser's local certificate warning so location permission can be used.
3. Allow Python through Windows Firewall on private networks if prompted.

If the phone says the page cannot be reached, open PowerShell as Administrator
on the host computer and run:

```powershell
New-NetFirewallRule -DisplayName "RideShare Flask 5000" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow -Profile Private
```

The server must keep running, and both devices must be on the same network.
Turn off mobile data temporarily on the phone if it switches away from Wi-Fi.

The app uses a temporary local HTTPS certificate so device location access is
available. The certificate is regenerated when the server restarts, so accept
the local certificate warning again if prompted.
Mobile browsers may block device location on this plain HTTP address. Use an
HTTPS tunnel or HTTPS reverse proxy when testing location from another device.

Data is stored in a local SQLite file `rideshare.db`, created automatically
on first run. Delete it any time to reset all accounts and rides.

## How to try it
- Open two browser windows (or one normal + one incognito).
- In window 1, sign up as a Passenger.
- In window 2, sign up as a Driver, then flip the "Online" switch.
- In window 1, book a ride (tick "pooled" for the discounted shared fare).
- In window 2, accept it under "Nearby requests", then Start Trip, then
  Complete Trip.
- Both sides get prompted to rate each other after completion — ratings
  show up as a ★ badge next to each profile from then on.
