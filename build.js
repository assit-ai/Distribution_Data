const fs = require('fs');
const path = require('path');

console.log('--- Netlify Custom Build Script Starting ---');

// 1. Ensure 'public' folder exists
if (!fs.existsSync('public')) {
  console.log("Creating missing 'public' directory...");
  fs.mkdirSync('public', { recursive: true });
}

// 2. Ensure 'netlify/functions' folder exists
if (!fs.existsSync('netlify/functions')) {
  console.log("Creating missing 'netlify/functions' directory...");
  fs.mkdirSync('netlify/functions', { recursive: true });
}

// 3. Ensure public/index.html exists
if (fs.existsSync('index.html')) {
  console.log("Syncing index.html to public/index.html...");
  fs.copyFileSync('index.html', 'public/index.html');
} else if (fs.existsSync('public/index.html')) {
  console.log("public/index.html already exists.");
}

// 4. Ensure public/_redirects exists
if (!fs.existsSync('public/_redirects')) {
  console.log("Creating public/_redirects file...");
  const redirectsContent = `/api/*  /.netlify/functions/api/:splat  200!\n/healthz  /.netlify/functions/api  200!\n/ping  /.netlify/functions/api  200!\n/*  /index.html  200\n`;
  fs.writeFileSync('public/_redirects', redirectsContent, 'utf8');
}

// 5. Ensure netlify/functions/api.js exists
if (!fs.existsSync('netlify/functions/api.js')) {
  console.log("Creating missing netlify/functions/api.js...");
  const apiJsContent = `'use strict';
const serverless = require('serverless-http');
const app = require('../../server.js');

module.exports.handler = serverless(app);
`;
  fs.writeFileSync('netlify/functions/api.js', apiJsContent, 'utf8');
}

// 6. Copy assets if present in root to public
const assets = [
  'favicon.ico', 'favicon-16x16.png', 'favicon-32x32.png',
  'apple-touch-icon.png', 'icon-192.png', 'icon-512.png',
  'icon.svg', 'manifest.json', 'sw.js'
];
assets.forEach(asset => {
  if (fs.existsSync(asset) && !fs.existsSync(path.join('public', asset))) {
    try {
      fs.copyFileSync(asset, path.join('public', asset));
      console.log(`Copied ${asset} to public/`);
    } catch (e) {
      console.error(`Failed to copy ${asset}:`, e.message);
    }
  }
});

console.log('--- Netlify Build Script Completed Successfully ---');
