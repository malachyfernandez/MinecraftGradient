# Minecraft Gradient Generator 🎨🧱

A highly customizable, visually interactive Minecraft block gradient generator. This web-based tool lets you create beautiful block gradients using real Minecraft textures, complete with exclusion filters, zooming, color interpolation, and randomized variations.

## 🌟 Features

- 🔲 **Real Minecraft Textures** — Uses actual block textures for maximum immersion.
- 🎚️ **Gradient Control Panel** — Adjust gradient length, randomness, and color interpolation.
- 🔍 **Block Selection Modal** — Search and pick any block for gradient endpoints.
- ❌ **Exclude Blocks** — Click-to-exclude system lets you filter out unwanted blocks.
- 🎨 **Live Preview** — Instantly see your gradient update as you tweak settings.
- 🎛️ **Options Panel** — Choose between "Separated" or "Condensed" layouts.
- 💾 **Preset Filters** — Built-in presets like “No Creative” or “No Inventories”.

## 🚀 Getting Started

### Option 1: Local Use

1. Clone or download the repository.
2. Open `index.html` in any modern web browser.
3. Start building your Minecraft gradients!

**OR**

### Option 2: Online Version (GitHub Pages)

1. Simply go to: [malachyfernandez.github.io/minecraftGradient](https://malachyfernandez.github.io/minecraftGradient/index.html)
2. The gradient generator will load automatically in your browser — no installation needed.
3. Enjoy experimenting with block gradients immediately.

> **What is a GitHub Page?**
> GitHub Pages is a free hosting service that allows you to publish static websites straight from a GitHub repository.&#x20;

## 🛠️ Tech Stack

- **HTML5 / CSS3** — Modern responsive UI with smooth transitions and custom styling.
- **JavaScript** — Handles color conversion (RGB ↔ LAB), gradient interpolation, UI state, and logic.
- **Minecraft Resource Pack** — Uses texture links from the official Minecraft resource repositories.

## 📁 File Structure

- `index.html` — Main file containing all markup, styling, and logic.
- Textures loaded directly via CDN from GitHub (ZtechNetwork/MCBVanillaResourcePack).

## 💡 Color Science

The gradient generation is powered by:

- RGB to LAB color space conversion
- Delta-E distance for perceptual color similarity
- Smooth LAB interpolation to generate pleasing gradients

## 🔧 Customization

- Adjust `randomness`, `gradient length`, and `block exclusions` to tailor your output.
- Zoom in/out to get a closer look at your gradient.
- Easily switch between layout modes in the options panel.

## 📜 License

This project is open-source. Feel free to use, modify, and share under your preferred license.

