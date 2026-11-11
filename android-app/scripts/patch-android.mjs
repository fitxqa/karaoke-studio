// After `npx cap add android`: microphone permission, orange/black theme, app icons, version.
import { readFileSync, writeFileSync, cpSync, rmSync, existsSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const app = join(here, '..', 'android', 'app');
const main = join(app, 'src', 'main');

// 1. permissions (microphone for singing) + all screen sizes/orientations
const manifestPath = join(main, 'AndroidManifest.xml');
let m = readFileSync(manifestPath, 'utf8');
const perms = [
  'android.permission.RECORD_AUDIO',
  'android.permission.MODIFY_AUDIO_SETTINGS',
  'android.permission.INTERNET',
  'android.permission.ACCESS_NETWORK_STATE',
];
for (const p of perms) {
  if (!m.includes(p)) m = m.replace('</manifest>', `    <uses-permission android:name="${p}" />\n</manifest>`);
}
if (!m.includes('android:largeHeap')) m = m.replace("<application", "<application android:largeHeap=\"true\" android:requestLegacyExternalStorage=\"true\" android:usesCleartextTraffic=\"true\"");
if (!m.includes('supports-screens')) {
  m = m.replace('</manifest>', '    <supports-screens android:smallScreens="true" android:normalScreens="true" android:largeScreens="true" android:xlargeScreens="true" android:anyDensity="true" />\n</manifest>');
}
m = m.replace(/android:screenOrientation="[^"]*"/g, '');
writeFileSync(manifestPath, m);

// 2. colours: black bars, orange accent
const values = join(main, 'res', 'values');
writeFileSync(join(values, 'colors.xml'), `<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="colorPrimary">#000000</color>
    <color name="colorPrimaryDark">#000000</color>
    <color name="colorAccent">#FF7A00</color>
</resources>
`);
const stylesPath = join(values, 'styles.xml');
if (existsSync(stylesPath)) {
  let st = readFileSync(stylesPath, 'utf8');
  st = st.replace(/<item name="android:background">[^<]*<\/item>/g, '');
  st = st.replace('</style>', '        <item name="android:statusBarColor">#000000</item>\n        <item name="android:navigationBarColor">#000000</item>\n        <item name="android:windowBackground">#000000</item>\n    </style>');
  writeFileSync(stylesPath, st);
}

// 3. icons: replace the default launcher icons with ours
const res = join(main, 'res');
for (const d of readdirSync(res)) {
  if (d.startsWith('mipmap-anydpi')) rmSync(join(res, d), { recursive: true, force: true });
}
cpSync(join(here, '..', 'resources', 'res'), res, { recursive: true });

// 4. version from CI run number
const gradlePath = join(app, 'build.gradle');
let g = readFileSync(gradlePath, 'utf8');
const code = process.env.VERSION_CODE || '1';
g = g.replace(/versionCode \d+/, `versionCode ${code}`).replace(/versionName "[^"]*"/, `versionName "1.4.${code}"`);
writeFileSync(gradlePath, g);
console.log('Android project patched');
