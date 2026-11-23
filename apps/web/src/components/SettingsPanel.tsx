interface SettingsPanelProps {
  ytBrowser: string;
  availableBrowsers: string[];
  onYtBrowserChange: (browser: string) => void;
}

export default function SettingsPanel({ ytBrowser, availableBrowsers, onYtBrowserChange }: SettingsPanelProps) {
  return (
    <div className="studio-section space-y-3">
      <h1 className="text-xl font-semibold text-white">Settings</h1>
      <div className="space-y-2">
        <h2 className="text-sm font-semibold text-white">YouTube authentication</h2>
        <label htmlFor="yt-browser" className="text-xs text-gray-300 block mb-1">Browser</label>
        <select
          id="yt-browser"
          value={ytBrowser}
          onChange={(e) => onYtBrowserChange(e.target.value)}
          className="studio-field"
        >
          <option value="none">None (anonymous)</option>
          <option value="firefox">Firefox {availableBrowsers.includes('firefox') ? '✓' : ''}</option>
          <option value="chrome">Google Chrome {availableBrowsers.includes('chrome') ? '✓' : ''}</option>
          <option value="brave">Brave {availableBrowsers.includes('brave') ? '✓' : ''}</option>
          <option value="edge">Microsoft Edge {availableBrowsers.includes('edge') ? '✓' : ''}</option>
        </select>
      </div>
    </div>
  );
}
