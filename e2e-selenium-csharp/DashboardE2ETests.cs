using OpenQA.Selenium;
using OpenQA.Selenium.Chrome;
using OpenQA.Selenium.Support.UI;
using Xunit;

namespace WikiPulse.E2E;

public class DashboardE2ETests : IDisposable
{
    private readonly IWebDriver _driver;
    private readonly WebDriverWait _wait;
    private readonly string _baseUrl;

    public DashboardE2ETests()
    {
        _baseUrl = Environment.GetEnvironmentVariable("WIKIPULSE_URL") ?? "http://localhost:5173";

        var options = new ChromeOptions();
        options.AddArgument("--headless=new");
        options.AddArgument("--no-sandbox");
        options.AddArgument("--disable-dev-shm-usage");
        options.AddArgument("--window-size=1440,1080");

        _driver = new ChromeDriver(options);
        _wait = new WebDriverWait(_driver, TimeSpan.FromSeconds(30));
        _wait.IgnoreExceptionTypes(typeof(StaleElementReferenceException));
    }

    [Fact]
    public void Dashboard_StreamsEdits_ShowsTopPages_PulseSummary_VandalismFlag_AndFiltersByWiki()
    {
        _driver.Navigate().GoToUrl(_baseUrl);

        // 1. Wait until live feed receives edits and EPM is non-zero
        _wait.Until(d =>
        {
            var items = d.FindElements(By.CssSelector("[data-testid='live-feed-item']"));
            var epmEl = d.FindElements(By.CssSelector("[data-testid='epm-value']")).FirstOrDefault();
            return items.Count > 0 && epmEl != null && int.TryParse(epmEl.Text.Replace(",", ""), out var val) && val > 0;
        });

        // 2. Verify Top 10 panel populates
        var topRows = _wait.Until(d =>
        {
            var rows = d.FindElements(By.CssSelector("[data-testid='top-page-row']"));
            return rows.Count > 0 ? rows : null;
        });
        Assert.NotNull(topRows);
        Assert.True(topRows!.Count >= 1);

        // 3. Switch to Replay mode (if currently Live) so deterministic vandalism samples are guaranteed
        var replayBtn = _driver.FindElement(By.CssSelector("[data-testid='mode-replay-btn']"));
        if (replayBtn.Enabled)
        {
            replayBtn.Click();
        }

        // 4. Wait for at least one vandalism flag banner with a reason, then verify Flagged Only toggle
        var flagBanner = _wait.Until(d =>
        {
            var banners = d.FindElements(By.CssSelector("[data-testid='vandalism-flag-banner']"));
            return banners.Count > 0 ? banners[0] : null;
        });
        Assert.NotNull(flagBanner);
        Assert.False(string.IsNullOrWhiteSpace(flagBanner!.Text));

        var flaggedToggle = _driver.FindElement(By.CssSelector("[data-testid='vandalism-only-toggle']"));
        flaggedToggle.Click();
        var flaggedItems = _driver.FindElements(By.CssSelector("[data-testid='live-feed-item']"));
        Assert.NotEmpty(flaggedItems);
        Assert.All(flaggedItems, item => Assert.Equal("true", item.GetAttribute("data-flagged")));
        flaggedToggle.Click();

        // 5. Refresh and verify AI Pulse Summary renders plain-English copy
        var refreshBtn = _driver.FindElement(By.CssSelector("[data-testid='refresh-pulse-btn']"));
        if (refreshBtn.Enabled)
        {
            refreshBtn.Click();
        }
        var pulseText = _wait.Until(d =>
        {
            var el = d.FindElement(By.CssSelector("[data-testid='pulse-summary-text']"));
            return !string.IsNullOrWhiteSpace(el.Text) &&
                   el.Text.Contains("edits/min", StringComparison.OrdinalIgnoreCase)
                ? el.Text
                : null;
        });
        Assert.NotNull(pulseText);
        Assert.Contains("edits/min", pulseText!, StringComparison.OrdinalIgnoreCase);

        // 6. Filter dashboard to enwiki via dropdown and verify visible edits match enwiki
        var selectElement = new SelectElement(
            _driver.FindElement(By.CssSelector("[data-testid='wiki-filter-select']"))
        );
        _wait.Until(d =>
        {
            var sel = new SelectElement(d.FindElement(By.CssSelector("[data-testid='wiki-filter-select']")));
            return sel.Options.Any(o => o.GetAttribute("value") == "enwiki");
        });
        selectElement.SelectByValue("enwiki");

        _wait.Until(d =>
        {
            var js = (IJavaScriptExecutor)d;
            var result = js.ExecuteScript(
                "const items = Array.from(document.querySelectorAll('[data-testid=\"live-feed-item\"]'));" +
                "return items.length > 0 && items.every(el => el.textContent.toLowerCase().includes('enwiki'));"
            );
            return result is bool ok && ok;
        });

        // 7. Switch back to All Wikis and verify Pause/Resume works
        _driver.FindElement(By.CssSelector("[data-testid='quick-wiki-all']")).Click();

        var pauseBtn = _driver.FindElement(By.CssSelector("[data-testid='pause-feed-btn']"));
        pauseBtn.Click();
        Assert.Contains("Resume", pauseBtn.Text, StringComparison.OrdinalIgnoreCase);
        pauseBtn.Click();
        Assert.Contains("Pause", pauseBtn.Text, StringComparison.OrdinalIgnoreCase);

        // 8. Switch back to Live Wikimedia mode and verify live connection recovers cleanly
        var liveBtn = _driver.FindElement(By.CssSelector("[data-testid='mode-live-btn']"));
        if (liveBtn.Enabled)
        {
            liveBtn.Click();
        }
        _wait.Until(d =>
        {
            var status = d.FindElement(By.CssSelector("[data-testid='connection-status']")).Text;
            return status.Contains("Live Wikimedia SSE", StringComparison.OrdinalIgnoreCase);
        });

        // 9. Verify zero severe console errors in the browser
        var logs = _driver.Manage().Logs.GetLog(LogType.Browser);
        var severeErrors = logs.Where(l => l.Level == LogLevel.Severe).ToList();
        Assert.Empty(severeErrors);
    }

    public void Dispose()
    {
        _driver.Quit();
        _driver.Dispose();
    }
}
