// KaraokeStudio.exe - starts the bundled Python server and opens the app in the browser.
// Compiled by build.bat with the C# compiler that ships with Windows (.NET Framework 4).
using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Threading;

class KaraokeLauncher
{
    const string Url = "http://localhost:8000";

    static int Main()
    {
        Console.Title = "Karaoke Studio";
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "python", "python.exe");

        // Already running (e.g. double-clicked twice)? Just open the browser.
        if (IsUp())
        {
            OpenBrowser();
            return 0;
        }
        if (!File.Exists(python))
        {
            Console.WriteLine("Python is missing from " + python + ". Please reinstall Karaoke Studio.");
            Console.ReadKey();
            return 1;
        }

        string dataRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "KaraokeStudio");
        Directory.CreateDirectory(dataRoot);

        Console.ForegroundColor = ConsoleColor.Cyan;
        Console.WriteLine();
        Console.WriteLine("  Karaoke Studio");
        Console.ResetColor();
        Console.WriteLine("  Keep this window open while you sing. Close it to stop.");
        Console.WriteLine("  Your songs are saved in " + Path.Combine(dataRoot, "data"));
        Console.WriteLine();

        // Keep the YouTube downloader fresh (YouTube changes often). Skipped quietly when offline.
        Console.WriteLine("  Checking for YouTube downloader updates...");
        Run(python, "-m pip install -U -q --disable-pip-version-check --no-warn-script-location yt-dlp",
            root, dataRoot, 90000, true);

        Console.WriteLine("  Starting...");
        // 0.0.0.0 = also reachable from your phone/tablet on the same Wi-Fi (Android app).
        // Set KARAOKE_LOCAL_ONLY=1 to keep it on this PC only.
        string host = Environment.GetEnvironmentVariable("KARAOKE_LOCAL_ONLY") == "1" ? "127.0.0.1" : "0.0.0.0";
        Process server = Start(python, "-m uvicorn app.main:app --host " + host + " --port 8000",
            Path.Combine(root, "server"), dataRoot, false);

        for (int i = 0; i < 180 && !server.HasExited; i++)
        {
            if (IsUp())
            {
                Console.ForegroundColor = ConsoleColor.Green;
                Console.WriteLine();
                Console.WriteLine("  Ready: " + Url);
                Console.ResetColor();
                foreach (string lan in LanAddresses())
                    Console.WriteLine("  Phone / tablet app (same Wi-Fi): " + lan);
                Console.WriteLine("  If Windows asks about the firewall, click Allow (Private networks).");
                Console.WriteLine();
                OpenBrowser();
                break;
            }
            Thread.Sleep(1000);
        }
        server.WaitForExit();
        if (server.ExitCode != 0)
        {
            Console.WriteLine();
            Console.WriteLine("  Karaoke Studio stopped with an error (see above). Press any key to close.");
            Console.ReadKey();
        }
        return server.ExitCode;
    }

    static ProcessStartInfo Info(string exe, string args, string cwd, string dataRoot, bool quiet)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        var psi = new ProcessStartInfo(exe, args);
        psi.WorkingDirectory = cwd;
        psi.UseShellExecute = false;
        psi.RedirectStandardOutput = quiet;
        psi.RedirectStandardError = quiet;
        psi.EnvironmentVariables["PATH"] =
            Path.Combine(root, "ffmpeg", "bin") + ";" + Path.Combine(root, "python") + ";" +
            Environment.GetEnvironmentVariable("PATH");
        psi.EnvironmentVariables["KARAOKE_DATA_DIR"] = Path.Combine(dataRoot, "data");
        psi.EnvironmentVariables["PYTHONUTF8"] = "1";
        psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
        return psi;
    }

    static Process Start(string exe, string args, string cwd, string dataRoot, bool quiet)
    {
        return Process.Start(Info(exe, args, cwd, dataRoot, quiet));
    }

    static void Run(string exe, string args, string cwd, string dataRoot, int timeoutMs, bool quiet)
    {
        try
        {
            using (Process p = Start(exe, args, cwd, dataRoot, quiet))
            {
                if (quiet)
                {
                    p.OutputDataReceived += (s, e) => { };
                    p.ErrorDataReceived += (s, e) => { };
                    p.BeginOutputReadLine();
                    p.BeginErrorReadLine();
                }
                if (!p.WaitForExit(timeoutMs))
                {
                    try { p.Kill(); } catch { }
                }
            }
        }
        catch { /* offline or blocked: carry on with the installed version */ }
    }

    static bool IsUp()
    {
        try
        {
            var req = (HttpWebRequest)WebRequest.Create(Url + "/health");
            req.Timeout = 1500;
            req.Proxy = null;
            using (req.GetResponse()) return true;
        }
        catch { return false; }
    }

    static string[] LanAddresses()
    {
        var list = new System.Collections.Generic.List<string>();
        try
        {
            foreach (var ip in Dns.GetHostEntry(Dns.GetHostName()).AddressList)
            {
                if (ip.AddressFamily != System.Net.Sockets.AddressFamily.InterNetwork) continue;
                string s = ip.ToString();
                if (s.StartsWith("127.") || s.StartsWith("169.254.")) continue;
                list.Add("http://" + s + ":8000");
            }
        }
        catch { }
        return list.ToArray();
    }

    static void OpenBrowser()
    {
        try { Process.Start(Url); } catch { }
    }
}
