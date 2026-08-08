OpenTutor custom fonts
======================

Lilita One loads automatically from Google Fonts. The other three are NOT
webfonts (dafont / Hoefler commercial), so they cannot be linked by URL.
Drop the font files here and the page picks them up via @font-face in index.html.

Expected filenames (any one format works; woff2 preferred for web):
  Nougat.woff2         (or Nougat.otf / Nougat.ttf)          https://www.dafont.com/nougat.font
  AcropolisBlack.woff2 (or AcropolisBlack.otf)               https://www.myfonts.com/collections/acropolis-font-hoefler-and-co
  SomeTimeLater.woff2  (or SomeTimeLater.otf / .ttf)         https://www.dafont.com/some-time-later.font

Tip: convert .otf/.ttf to .woff2 at https://cloudconvert.com/ttf-to-woff2 for faster loads.

Until the files are present the page falls back to:
  display/headings -> Lilita One
  body/UI          -> Baloo 2 / system rounded sans
  handwriting      -> Comic Sans / system cursive

Role mapping (see :root font vars in index.html):
  --f-display  Acropolis Black -> big hero + XP numbers
  --f-head     Lilita One      -> headings & buttons
  --f-ui       Nougat          -> body text, labels, buttons
  --f-hand     Some Time Later -> handwritten camera notes
