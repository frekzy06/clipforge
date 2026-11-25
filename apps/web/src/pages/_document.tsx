import { Html, Head, Main, NextScript } from 'next/document'

export default function Document() {
  return (
    <Html lang="en">
      <Head>
        {/* Installed font families are served by the API as generated
            @font-face rules so the preview uses the exact same font files
            that FFmpeg burns into the export.

            This has to live in `_document`: Next.js 16 rejects stylesheets
            added through `next/head` and warns about them. */}
        <link rel="stylesheet" href="/api/fonts/css" />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  )
}
