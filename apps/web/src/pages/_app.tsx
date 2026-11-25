import './globals.css'
import type { AppProps } from 'next/app'

export default function App({ Component, pageProps }: AppProps) {
  // Installed font families are served by the API as generated @font-face
  // rules so the preview uses the exact same font files that FFmpeg burns
  // into the export. The stylesheet is linked from `pages/_document.tsx`:
  // Next.js 16 warns when a stylesheet is added through `next/head`.
  return <Component {...pageProps} />
}
