"""Keep FastDL's helpers up to date. yt-dlp breaks whenever YouTube changes, so update often.

python update_deps.py               update everything now
python update_deps.py --schedule    also run it every day at 12:00 (Windows Task Scheduler)
python update_deps.py --unschedule  stop the daily run

Log: ~/.fastdl/update.log
"""
import datetime, os, subprocess, sys

WINGET = ["aria2.aria2", "Gyan.FFmpeg", "DenoLand.Deno"]
TASK = "FastDL update"
LOG = os.path.join(os.path.expanduser("~"), ".fastdl", "update.log")
PYLIB = os.path.join(os.path.expanduser("~"), ".fastdl", "pylib")  # newer yt-dlp that FastDL.exe prefers
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console flashing when run by the scheduler


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    return r.returncode, (r.stdout + r.stderr).strip()


def update():
    results = []
    # [default] pulls in yt-dlp-ejs, which YouTube now needs together with deno
    code, out = run([sys.executable, "-m", "pip", "install", "-U", "--quiet", "--disable-pip-version-check", "yt-dlp[default]"])
    _, ver = run([sys.executable, "-m", "yt_dlp", "--version"])
    results.append(("yt-dlp", "ok " + ver if code == 0 else "FAILED: " + out[-300:]))
    # FastDL.exe carries its own yt-dlp, frozen at build time; it loads this folder first instead
    code, out = run([sys.executable, "-m", "pip", "install", "-U", "--quiet", "--disable-pip-version-check",
                     "--target", PYLIB, "yt-dlp[default]"])
    results.append(("yt-dlp for exe", "ok" if code == 0 else "FAILED: " + out[-300:]))
    for pkg in WINGET:
        code, out = run(["winget", "upgrade", "--id", pkg, "-e", "--silent",
                         "--accept-source-agreements", "--accept-package-agreements"])
        if code == 0:
            status = "updated"
        elif "No available upgrade" in out or "No newer package" in out or "No applicable" in out:
            status = "up to date"  # winget reports "nothing to do" as an error code
        else:
            status = "FAILED (close FastDL if it is in use): " + out.splitlines()[-1][:200] if out else "FAILED"
        results.append((pkg, status))

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        for name, status in results:
            line = f"{stamp}  {name:15} {status}"
            print(line)
            f.write(line + "\n")
    return all("FAILED" not in s for _, s in results)


def schedule():
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")  # pythonw = no console window
    exe = pyw if os.path.exists(pyw) else sys.executable
    # ponytail: daily at 12:00, skipped if the PC is off then; switch to an XML task with "run when missed" if that bites
    code, out = run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", "12:00", "/TN", TASK,
                     "/TR", f'"{exe}" "{os.path.abspath(__file__)}"'])
    print(out)
    return code == 0


if __name__ == "__main__":
    if "--unschedule" in sys.argv:
        print(run(["schtasks", "/Delete", "/F", "/TN", TASK])[1])
        sys.exit()
    ok = update()
    if "--schedule" in sys.argv:
        ok = schedule() and ok
    sys.exit(0 if ok else 1)
