# Third-party software in Sound Control

Sound Control source code is MIT, copyright (c) 2026 AndZas.
The MIT licence does not replace licences of the components below.

## Qt / PySide6 / Shiboken

The interface uses Qt for Python 6.12.0, selected here under LGPL-3.0-only.
The application bundles dynamically loaded Qt Core, Gui, Widgets, Network,
OpenGL and SVG libraries. Unused Qt PDF, QML/Quick and Virtual Keyboard
libraries/plugins are excluded from the distribution.

Full LGPLv3 and GPLv3 texts are in `licenses`. Original upstream third-party
notices are preserved in `licenses/Qt-third-party`.
You may modify or replace these libraries, including reverse engineering
needed to debug such changes. There is no restriction on running a rebuilt
application with compatible modified libraries.

The release includes `SoundControl-1.0.0-source.zip` with the complete
application source/build recipe and `SoundControl-1.0.0-qt-sources.zip`
with the corresponding, unmodified Qt Base, Qt SVG and PySide/Shiboken sources.
Exact upstream URLs and SHA-256 values: `docs/QT-SOURCES.json` in the source
package, also copied into the Qt source archive. Rebuilding instructions:
`docs/BUILDING.md`. No proprietary application code or signing key is needed.

Official licensing: https://www.qt.io/development/open-source-lgpl-obligations
Qt for Python source: https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.12.0-src/
Qt module source: https://download.qt.io/official_releases/qt/6.12/6.12.0/submodules/

## Other runtime and build components

Python uses its PSF licence; keyboard, pycaw and comtypes use their included
permissive licences; psutil uses BSD; Pillow uses its included PIL/Pillow
licence. PyInstaller uses GPL with its bootloader distribution exception.
Exact versions and verbatim notices are in `licenses/DEPENDENCIES.json` and
the corresponding text files. Bundled Qt/Python third-party notices retain
their original copyright and licence conditions.

These libraries are not authored by Sound Control. No modifications are made
to the distributed Qt/Python libraries by this project.

## VoiceMeeter Banana (optional offline installation pack)

VoiceMeeter Banana is a separate VB-Audio product by Vincent Burel.
Official origin: https://voicemeeter.com and https://vb-audio.com/Voicemeeter/banana.htm

VoiceMeeter Banana is donationware. Contributions and licence payments are
welcome when you find it useful. The end user can open the original mixer and
its About/licensing interface independently of Sound Control.
Support the author / purchase a licence:
https://shop.vb-audio.com/en/win-apps/10-voicemeeter-banana.html

The optional pack redistributes the original `VoicemeeterSetup_v2130.zip` AS IS,
with SHA-256 `ee8b1f6cd4728233c8ae52897128c9af02367c091b88f55a1996d424e04d3965`.
Its contents are not modified. Installing it also installs the Standard edition
and virtual audio drivers. The original VB-Audio terms apply to this component;
Sound Control does not grant a VB-Audio licence or include a paid licence key.

Distribution/use conditions, including professional and organizational use:
https://vb-audio.com/Services/licensing.htm
https://shop.vb-audio.com/en/content/3-Terms-of-use

VB-Audio retains its copyrights and trademarks. Sound Control is an independent
utility, not an official VB-Audio product. It uses the publicly available
VoiceMeeter Remote API; audio drivers and the mixer belong to VB-Audio.

The three built-in Sound Control WAVs are original synthesized recordings:
see `audio_assets/README.md` in the source package.
