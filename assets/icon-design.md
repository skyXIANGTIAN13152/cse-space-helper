# CSE 空间助手图标

## 当前设计：空心终端盾牌（2026-10-12）

参考 HypoMux 的透明底线描语言，保留 CSE 空间助手的盾牌、终端提示符与清理星芒，重新绘制为原创矢量图标。取消黄色底块及盾牌内部填色；背景和盾牌内部均为真实透明。

线条采用琥珀橙 `#F4AB4C` → 日落橙 `#F89B53` → 珊瑚红 `#F17861`，与 HypoMux 的蓝紫色明确区分。轮廓在右上角留出星芒位置，保持小尺寸辨识度；无文字、外阴影或底板。界面采用暖石墨 `#211E1C`、棕灰描边和同色系交互强调。

当前资产：`cse-outline.svg` 为可编辑矢量源，`cse-outline.png` 为 1024px 透明预览，项目根目录 `app.ico` 包含 16、20、24、32、40、48、64、96、128、256px 十种尺寸。安装目录使用新图标文件名以避免旧图标缓存。图标由 SVG 矢量渲染，不对旧生成图片做像素修改。

## 历史设计：黄色底块

采用用户确认的第二次生成结果。UNSW 参考图提供黄色和盾形轮廓；终端参考图提供蓝色、白色大于号和浅蓝色下划线。星芒表示清理。

生成方式：内置 image_gen；未使用 CLI/API 回退。生成 PNG 的透明通道保留，ICO 仅进行格式转换和 Windows 所需尺寸缩放。

文件：`cse-terminal-icon.png` 为生成原图，项目根目录 `app.ico` 为应用图标；`app-previous.ico` 为原图标备份。

## 首次生成提示

Create one polished, original Windows desktop application icon for a university CSE disk-space cleaning helper. Use the two attached reference images as design references, not as a collage. Reference 1 contributes UNSW's vivid golden-yellow and black palette and the strong heraldic shield silhouette; simplify those visual cues substantially, do not reproduce its tiny lettering or elaborate lion. Reference 2 contributes the royal-blue terminal surface and the bold, crisp white greater-than prompt with a light-blue underscore (>_). Unify these into a single coherent icon: a generous golden-yellow rounded-square app tile, with a bold dark-navy outlined shield centered inside, a rich royal-blue shield interior, and a very large clean white > plus pale-blue underscore integrated in the center of the shield. The shield should have a confident flat upper edge and a rounded pointed bottom reminiscent of the academic crest. Add just one small golden four-point sparkle inside the upper-right area of the blue shield, hinting at cleaning and free space, without competing with the terminal prompt. Strong optical balance, bold geometric shapes, premium flat vector-like execution, minimal outlines, very subtle depth at most, no glossy 3D. Readable and distinctive at 16, 32, and 48 pixels. Broad margins around the shield inside the tile. No words, letters, school name, wordmarks, numbers, explanatory text, watermark, extra objects, or mockup; the only typographic marks are > and _. One square 1024x1024 composition, centered and nearly filling the canvas, with genuine transparent background outside the rounded-square tile. Deliver the finished icon alone, not a presentation sheet.

## 最终修整提示

Polish this exact desktop app icon. Preserve the existing composition, yellow rounded-square tile, dark-navy academic shield outline, royal-blue shield interior, white > prompt, pale-blue underscore, and single golden four-point sparkle. Keep all shapes, colors, and relative positions essentially unchanged. The requested change is ONLY professional edge cleanup and simplification: remove every stray yellow speck and fleck around the outside perimeter, eliminate the rough/frayed borders, and produce perfectly smooth clean antialiased rounded-square edges with completely empty transparent pixels outside. No texture, grain, distressing, scattered particles, shadow outside the tile, or marks beyond the tile. Use even, solid flat fills instead of mottling. Keep an even transparent margin on all four sides. One centered square 1024x1024 icon asset with genuine alpha transparency, not a mockup or checkerboard, no text added.
