"""Offline app-name hints: coarse defaults, never the user's actual intent."""
import ntpath


def normalize_app(value):
    if not isinstance(value, str) or len(value) > 250 or any(ord(char) < 32 for char in value):
        return ''
    return ntpath.basename(value.strip()).casefold()


# Exact executable names only. No title, URL, telemetry or fuzzy-name matching.
GROUPS = (
    ('编程', '代码编辑与开发', 'code.exe cursor.exe pycharm.exe pycharm64.exe idea.exe idea64.exe webstorm.exe webstorm64.exe devenv.exe eclipse.exe rider64.exe clion64.exe goland64.exe phpstorm64.exe androidstudio.exe studio64.exe sublime_text.exe atom.exe notepad++.exe zed.exe'),
    ('编程', '开发与调试工具', 'gitkraken.exe githubdesktop.exe sourcetree.exe postman.exe insomnia.exe fiddler.exe fiddlerclassic.exe wireshark.exe dbeaver.exe datagrip64.exe heidisql.exe mysqlworkbench.exe pgadmin4.exe'),
    ('学习', '阅读与笔记', 'obsidian.exe onenote.exe evernote.exe notion.exe logseq.exe joplin.exe typora.exe sumatrapdf.exe acrord32.exe acrobat.exe foxitpdfreader.exe zotero.exe calibre.exe xmind.exe anki.exe'),
    ('工作', '文档与表格办公', 'winword.exe excel.exe powerpnt.exe outlook.exe wps.exe wpp.exe et.exe soffice.exe soffice.bin'),
    ('娱乐', '影音播放', 'vlc.exe potplayer.exe potplayermini.exe potplayermini64.exe mpv.exe mpc-hc.exe mpc-hc64.exe mpc-be.exe mpc-be64.exe wmplayer.exe spotify.exe cloudmusic.exe qqmusic.exe kugou.exe bilibili.exe'),
    ('游戏', '游戏平台或游戏', 'steam.exe steamwebhelper.exe epicgameslauncher.exe battle.net.exe riotclientservices.exe minecraft.exe minecraftlauncher.exe prismLauncher.exe multimc.exe playnite.desktopapp.exe'),
    ('游戏', '游戏模拟器', 'mumunxdevice.exe mumuplayer.exe mumuplayerpro.exe mumumanager.exe nox.exe noxplayer.exe ldplayer.exe dnplayer.exe bluestacks.exe hd-player.exe'),
    ('生活', '待办与时间安排', 'ticktick.exe todoist.exe microsoft.todos.exe'),
    ('浏览器', '浏览器，多种用途', 'chrome.exe msedge.exe firefox.exe brave.exe opera.exe vivaldi.exe arc.exe browser.exe'),
    ('其他', 'AI助手，多种用途', 'chatgpt.exe claude.exe codex.exe deepseek.exe doubao.exe yuanbao.exe kimi.exe'),
    ('其他', '通讯应用，多种用途', 'weixin.exe wechat.exe wechatappex.exe qq.exe tim.exe telegram.exe discord.exe slack.exe teams.exe ms-teams.exe feishu.exe dingtalk.exe whatsapp.exe signal.exe zoom.exe'),
    ('其他', '图像与视频创作，多种用途', 'photoshop.exe lightroom.exe lightroomclassic.exe premiere.exe afterfx.exe resolve.exe capcut.exe jianyingpro.exe blender.exe obs64.exe obs32.exe figma.exe'),
    ('其他', '终端或脚本运行，多种用途', 'javaw.exe java.exe python.exe pythonw.exe node.exe cmd.exe powershell.exe pwsh.exe windowsterminal.exe wt.exe'),
    ('其他', '文件管理与系统界面', 'explorer.exe startmenuexperiencehost.exe searchhost.exe applicationframehost.exe shellexperiencehost.exe systemsettings.exe control.exe rundll32.exe taskmgr.exe'),
)

CATALOG = {}
for category, hint, names in GROUPS:
    for name in names.split():
        CATALOG[name.casefold()] = (category, hint)
# Executables containing spaces cannot use the space-delimited groups above.
for name, category, hint in (('docker desktop.exe', '编程', '开发与容器工具'),
                             ('davinci resolve.exe', '其他', '视频创作，多种用途'),
                             ('clash for windows.exe', '其他', '网络连接工具')):
    CATALOG[name] = (category, hint)

for name in ('codex.exe', 'windsurf.exe', 'trae.exe', 'code-insiders.exe', 'opencode.exe'):
    CATALOG[name] = ('编程', '编程与AI开发工具')

DISPLAY_NAMES = {
    'code.exe': 'Visual Studio Code', 'code-insiders.exe': 'Visual Studio Code Insiders',
    'cursor.exe': 'Cursor', 'codex.exe': 'Codex', 'windsurf.exe': 'Windsurf', 'trae.exe': 'Trae',
    'pycharm64.exe': 'PyCharm', 'pycharm.exe': 'PyCharm', 'idea64.exe': 'IntelliJ IDEA',
    'devenv.exe': 'Visual Studio', 'notepad++.exe': 'Notepad++', 'postman.exe': 'Postman',
    'chatgpt.exe': 'ChatGPT', 'claude.exe': 'Claude', 'deepseek.exe': 'DeepSeek',
    'doubao.exe': '豆包', 'yuanbao.exe': '腾讯元宝', 'kimi.exe': 'Kimi',
    'weixin.exe': '微信', 'wechat.exe': '微信', 'qq.exe': 'QQ', 'ms-teams.exe': 'Microsoft Teams',
    'ticktick.exe': '滴答清单', 'todoist.exe': 'Todoist',
    'mumunxdevice.exe': 'MuMu 模拟器', 'mumuplayer.exe': 'MuMu 模拟器', 'mumumanager.exe': 'MuMu 模拟器',
    'steam.exe': 'Steam', 'steamwebhelper.exe': 'Steam', 'epicgameslauncher.exe': 'Epic Games',
    'chrome.exe': 'Google Chrome', 'msedge.exe': 'Microsoft Edge', 'firefox.exe': 'Firefox',
    'winword.exe': 'Microsoft Word', 'excel.exe': 'Microsoft Excel', 'powerpnt.exe': 'Microsoft PowerPoint',
    'wps.exe': 'WPS 文字', 'wpp.exe': 'WPS 演示', 'et.exe': 'WPS 表格',
    'obsidian.exe': 'Obsidian', 'onenote.exe': 'OneNote', 'notion.exe': 'Notion',
    'sumatrapdf.exe': 'SumatraPDF', 'acrord32.exe': 'Adobe Acrobat Reader', 'zotero.exe': 'Zotero',
    'vlc.exe': 'VLC 播放器', 'cloudmusic.exe': '网易云音乐', 'qqmusic.exe': 'QQ 音乐',
    'bilibili.exe': '哔哩哔哩', 'photoshop.exe': 'Adobe Photoshop', 'blender.exe': 'Blender',
    'javaw.exe': 'Java 程序', 'java.exe': 'Java 程序', 'python.exe': 'Python 程序',
    'powershell.exe': 'PowerShell', 'pwsh.exe': 'PowerShell', 'windowsterminal.exe': 'Windows 终端',
    'explorer.exe': '文件资源管理器', 'systemsettings.exe': 'Windows 设置',
    'taskmgr.exe': '任务管理器', 'clash for windows.exe': 'Clash for Windows',
}


def describe_app(value):
    name = normalize_app(value)
    category, hint = CATALOG.get(name, ('其他', '用途待确认'))
    return {'app_name': name, 'display_name': DISPLAY_NAMES.get(name, value), 'category': category, 'hint': hint,
            'recognized': name in CATALOG, 'multi_purpose': category in ('其他', '浏览器')}
