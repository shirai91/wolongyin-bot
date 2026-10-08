// livectl: tiny helper to inspect and drive the live game in Coowon, in the background.
// Build: C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /r:System.Drawing.dll /out:tools\live\livectl.exe tools\live\livectl.cs
//
// Usage (all coordinates are CLIENT coordinates of the Coowon top-level window, not canvas coordinates):
//   livectl info                      print hwnd, window rect, client size of the Coowon game window
//   livectl capture <out.png>         PrintWindow(PW_RENDERFULLCONTENT) of the client area
//   livectl click <x> <y>             background left click (WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP)
//   livectl dblclick <x> <y>          background double click
//   livectl key <ESC|ENTER|vk-number> background key press (WM_KEYDOWN + WM_KEYUP)
//
// The game window is the visible top-level "Chrome_WidgetWin_1" whose title contains "Coowon" and "Ng"
// (e.g. "Sv 266 Ngọa Long Việt Nam - Coowon Browser"). This Coowon build has no
// Chrome_RenderWidgetHostHWND child: input and capture both target the top-level window.
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

static class LiveCtl
{
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern bool GetClientRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")] static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
    [StructLayout(LayoutKind.Sequential)] struct RECT { public int L, T, R, B; }

    static IntPtr FindGame()
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows((h, l) =>
        {
            if (!IsWindowVisible(h)) return true;
            var t = new StringBuilder(512); GetWindowText(h, t, 512);
            var c = new StringBuilder(256); GetClassName(h, c, 256);
            string title = t.ToString();
            if (c.ToString() == "Chrome_WidgetWin_1" && title.Contains("Coowon") && title.Contains("Ng")) { found = h; return false; }
            return true;
        }, IntPtr.Zero);
        if (found == IntPtr.Zero) throw new Exception("Coowon game window not found");
        return found;
    }

    static IntPtr LParam(int x, int y) { return new IntPtr((y << 16) | (x & 0xFFFF)); }

    static int Main(string[] a)
    {
        try
        {
            var h = FindGame();
            switch (a.Length > 0 ? a[0] : "info")
            {
                case "info":
                {
                    RECT w, c; GetWindowRect(h, out w); GetClientRect(h, out c);
                    var t = new StringBuilder(512); GetWindowText(h, t, 512);
                    Console.WriteLine("hwnd=0x{0:X} title=\"{1}\" window=({2},{3})-({4},{5}) client={6}x{7}", h.ToInt64(), t, w.L, w.T, w.R, w.B, c.R, c.B);
                    break;
                }
                case "capture":
                {
                    RECT c; GetClientRect(h, out c);
                    using (var bmp = new Bitmap(c.R, c.B, PixelFormat.Format32bppArgb))
                    using (var g = Graphics.FromImage(bmp))
                    {
                        IntPtr hdc = g.GetHdc();
                        try { PrintWindow(h, hdc, 2); } finally { g.ReleaseHdc(hdc); }
                        bmp.Save(a[1], ImageFormat.Png);
                    }
                    Console.WriteLine("captured " + a[1]);
                    break;
                }
                case "click":
                case "dblclick":
                {
                    int x = int.Parse(a[1]), y = int.Parse(a[2]);
                    var lp = LParam(x, y);
                    PostMessage(h, 0x0200, IntPtr.Zero, lp); Thread.Sleep(40);
                    PostMessage(h, 0x0201, new IntPtr(1), lp); Thread.Sleep(50);
                    PostMessage(h, 0x0202, IntPtr.Zero, lp);
                    if (a[0] == "dblclick")
                    {
                        Thread.Sleep(50);
                        PostMessage(h, 0x0203, new IntPtr(1), lp); Thread.Sleep(50);
                        PostMessage(h, 0x0202, IntPtr.Zero, lp);
                    }
                    Console.WriteLine(a[0] + " " + x + "," + y);
                    break;
                }
                case "key":
                {
                    int vk = a[1] == "ESC" ? 0x1B : a[1] == "ENTER" ? 0x0D : int.Parse(a[1]);
                    PostMessage(h, 0x0100, new IntPtr(vk), IntPtr.Zero); Thread.Sleep(50);
                    PostMessage(h, 0x0101, new IntPtr(vk), new IntPtr(unchecked((int)0xC0000001)));
                    Console.WriteLine("key " + a[1]);
                    break;
                }
                default:
                    Console.WriteLine("unknown command"); return 2;
            }
            return 0;
        }
        catch (Exception e) { Console.Error.WriteLine(e.Message); return 1; }
    }
}
