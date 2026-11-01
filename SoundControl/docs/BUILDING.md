# Разработка и сборка

## Окружение

Windows x64 и Python 3.13 x64. Команды выполняются из папки `SoundControl`.

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -v
```

Тесты используют подставные устройства и Qt offscreen: установка Banana, запуск
аудиодвижка, смена оборудования и второй Login в SDK для тестов не требуются.
Для ручного запуска исходников:

```powershell
.\.venv\Scripts\python.exe voicemeeter_right_ctrl_mute.py
```

## Сборка EXE

```powershell
.\build.ps1 -Public
```

Результат: `dist-public/VoicemeeterMuteTray.exe`. Версия для окна, свойств Windows
и архивов берётся из **`version.py`**. Иконка и три WAV генерируются из исходников.
Рецепт `SoundControl.spec` ограничивает поставляемые Qt-модули используемыми
Widgets/SVG-компонентами; PDF, QML/Quick и Virtual Keyboard в релиз не входят.
Сценарий изолирует PATH от сторонних DLL и прикладывает лицензии зависимостей.

Для локальной рабочей копии:

```powershell
.\build.ps1 -OutputDirectory dist
```

Для подготовки всех файлов релиза:

```powershell
.\.venv\Scripts\python.exe package_release.py --download
```

Первый запуск загрузит проверенный официальный архив Banana и соответствующие
исходники Qt/PySide6 в игнорируемую папку `vendor`. Последующие сборки используют
локальный кэш и проверяют SHA-256. Итоговые файлы находятся в `release`.
Сценарий не вызывает `git add`, commit, tag, push и не обращается к GitHub API.

## Устройство проекта

| Файлы | Ответственность |
| --- | --- |
| `voicemeeter_right_ctrl_mute.py`, `local_control.py` | Жизненный цикл, один рабочий поток SDK и локальные управляющие сигналы |
| `settings_window.py`, `ui_theme.py`, `window_frame.py`, `tray_menu.py` | Qt-интерфейс, оформление, нативная рамка Windows и трей |
| `mic_recovery.py`, `bluetooth_outputs.py`, `banana_host.py` | Микрофон, выбранные выходы и управление Banana |
| `sounds.py`, `wasapi_output.py`, `generate_sounds.py` | Очередь сигналов, WASAPI и генерация WAV |
| `config.py`, `hotkeys.py`, `power_lifecycle.py`, `quiet_notice.py` | Профиль, клавиатура, сон/пробуждение и уведомления |
| `installer.py`, `package_*.py`, `SoundControl.spec`, `tools/` | Установка, проверка и упаковка релизов |
| `test_*.py` | Регрессионные тесты |
| `docs/`, `licenses/`, `audio_assets/` | Руководства, уведомления о лицензиях и исходные ресурсы |

## Изменение Qt-библиотек

Приложение использует динамические Qt-библиотеки под LGPLv3. Разрешены их
изменение, замена и обратная разработка для отладки таких изменений. Исходники
поставленных Qt Base, Qt SVG и PySide/Shiboken перечислены в `QT-SOURCES.json`
и приложены к релизу архивом `qt-sources`.

Соберите совместимые Qt/PySide6 согласно их инструкциям, установите полученные
wheel-пакеты в это окружение вместо стандартных и повторите `build.ps1`.
Исходники приложения, рецепт сборки и все требуемые ресурсы доступны в архиве
`source`; подпись/ключ или закрытая часть приложения для пересборки не нужны.

## Границы автоматической проверки

CI проверяет код и состав EXE на Windows x64. Установка нового аудиодрайвера Banana,
реальные USB/Bluetooth-переподключения и сон проверяются отдельно на тестовой Windows.
Сборка не заявляет Win32/ARM64 и не меняет устройства Windows по умолчанию.
