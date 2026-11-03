# Vinculum · 情感與印象卡

> 情感系統：熟悉度、好感、心情、印象；每晚從記憶裡回味和每個人的片段，算出各種感情、整理「心裡的 TA」、記下態度；群友要「印象卡」時發一張印象卡圖片

AstrBot 插件，屬於「擬真群友」（lively）插件套件。適用 AstrBot >=4.29.0 + NapCat（aiocqhttp）。

主人、群、機器人名字、人設用詞這些共用設定在 [astrbot_plugin_lively_gate](https://github.com/GUCine/astrbot_plugin_lively_gate) 裡填，建議一起裝。

## 功能

```text
機器人的好感度：熟悉度、好感、心情、印象、好久不見，以及機器人自己此刻的狀態。

情感：每晚從 LivingMemory 的記憶裡回味出和每個人的片段，由片段算出長期感情（親近、逗趣、牽掛、欣賞、芥蒂、反感；說話時換成「〈我〉跟 TA：…」一句字），
整理「心裡的 TA」（稱呼、印象、梗、語氣、牽掛；私聊裡知道的事只放「私下」），記下態度（正常、冷淡、戒備、冷處理、封閉）。
主人可以用指令查看；說話時用的是好感度（relation.py）、態度、心情和心裡的 TA（還沒有的人帶聊天時記的印象），
開了 inject_heart 再帶一兩段跟這句話沾邊的片段。聊天時記下的新印象馬上換進心裡的 TA

關係讀口：其他插件問「〈我〉對 TA 怎麼樣」用 relation(qq) / relations()（見 relation.py），不要自己讀 bond.db

每週人物卡 10-24 停了：和心裡的 TA 欄位重疊、兩份各寫各的（雷區對不上），說話只讀心裡的 TA。
cards 表留著當舊資料，停之前只有卡的人搬進心裡的 TA（_retire_cards）
```

## 依賴

建議裝 [LivingMemory](https://github.com/lxfight/astrbot_plugin_livingmemory)：每晚回味（片段、各種感情、心裡的 TA、態度）和做人物卡都讀它的記憶和聊天記錄，沒裝就只有好感、心情和印象。

## 素材

卡片的手寫字體不隨倉庫發佈：從 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 取得後放到 `card_assets/rt.woff2`；沒放就用中文黑體（Debian/Ubuntu 裝 `fonts-noto-cjk`）。名字、印象裡的表情符號（🍮）要畫出來，得有彩色表情字體：從 [googlefonts/noto-emoji](https://github.com/googlefonts/noto-emoji)（OFL 授權）取 `2D/fonts/NotoColorEmoji.ttf` 放到 `card_assets/NotoColorEmoji.ttf`（或 Debian/Ubuntu 裝 `fonts-noto-color-emoji`）；沒有就把表情拿掉，不畫成方框。`card_assets/fish.webp` 是預設角色的形象圖，可以換成自己的；`card_assets/feed/bg1.webp`～`bg5.webp` 是投喂卡五檔的背景畫（預設角色，生圖畫的），也可以換成自己的直式圖。

## 配置

在 AstrBot WebUI 的插件配置裡填，或者直接編輯 `data/config/astrbot_plugin_lively_bond_config.json`。留空的公共項（主人、群、機器人名字、平台）會用 astrbot_plugin_lively_gate 裡填的。

| 配置項 | 說明 | 預設 |
| --- | --- | --- |
| `provider_id` | 回味用的模型：每晚回味記憶、整理心裡的 TA 時用。留空用 AstrBot 預設模型 | （空） |
| `digest_persona` | 回味時帶上人設：每晚回味（拆片段、整理心裡的 TA、偏心）時，把人設原文和主人定的規矩（自我成長插件的 owner_rules）放在提示詞最前面，讓她按自己的性子判斷；每次都一樣，DeepSeek 前綴快取幾乎不另收錢。關掉就只用回味自己的規則 | true |
| `groups` | 記錄好感的群：留空就用門禁插件裡填的群 | （空） |
| `owner_qq` | 主人 QQ：主人好感和熟悉度固定是滿的，也只有主人能用 /好感 指令。留空用門禁插件裡填的 | （空） |
| `card_auto` | 群友要印象卡時自動發：有人叫機器人、而且是在問它對自己的印象時，認出來是在要印象卡（語義判斷，不用關鍵詞）。認出來之後發不發看下一項 | true |
| `card_ask` | 認出來先問機器人自己：開著：認出有人在要印象卡時只提醒機器人，發不發由它自己決定（不想給、覺得不是在要就不發）。關掉：認出來就直接發（舊做法） | true |
| `embedding_provider_id` | 判斷用的向量模型：填向量模型的 ID，留空用第一個向量模型（比如 LivingMemory 用的那個） | （空） |
| `card_examples` | 算在要印象卡的例句：起步例句，意思像這些就算；機器人還會自己學新說法 | ["你对我的印象怎么样", "给我看看我的印象卡", "我在你心里是什么样…" |
| `card_not_examples` | 不算要印象卡的例句：話題像但不是要卡的（問別人、誇機器人、看機器人自己的東西、別的卡、聊好感度這個話題），用來排除 | ["你对他的印象怎么样", "你觉得她这个人怎么样", "我对你的印象很好…" |
| `card_min_sim` | 至少要多像：和要卡例句的相似度門檻（0～1），調高更嚴 | 0.7 |
| `card_margin` | 比反例高多少才算：和要卡例句的相似度要比和反例的高出這麼多 | 0.05 |
| `card_keywords` | 先篩的字眼：一句話裡一個都沒有、也不像學到的要卡例句時，不去算向量（省一次向量請求）。留空用預設 | ["印象", "卡", "好感", "心里", "心中", "眼里", "…" |
| `card_learn` | 自己學新說法：模型自己判斷出有人在要卡、而例句沒認出來時，把那句話記成新例句 | true |
| `feed_card` | 投喂檔位卡：飯錢本上累計投喂到哪一檔，TA 的印象卡就換成那一檔的樣子：整張小肥魚的背景畫（card_assets/feed/bg1～5，可以換成自己的）、霧面玻璃、圓形頭像，第四、五檔是動圖；說話時也帶上檔名。卡上不寫金額。關掉就不分檔 | true |
| `feed_tiers` | 投喂檔位：一行一檔「累計金額/名字」，從小到大最多 5 檔。1 清晨淺灘、象牙白頭像圈；2 月下海面、深色玻璃、銀圈；3 淺海光束、珍珠圈；4 黃昏金海、香檳金圈和細框、金字、慢慢掃過一道光（動圖）；5 深海星河、深色玻璃配金、掃光、光點往上飄（動圖） | "1\|一口饭\n10\|一碗饭\n30\|一锅饭\n100\|长期饭票\n300…" |
| `reflect` | 每晚回味記憶：凌晨把 LivingMemory 的新記憶拆成和每個人的片段，算出各種感情、整理「心裡的 TA」、記下態度。第一階段只記錄，不影響說話 | true |
| `mention_people` | 話裡提到的人也帶上：消息裡 @ 到的人、單獨寫出來的 QQ 號，說話時多帶一行「這個號是誰、〈我〉和 TA 怎麼樣、心裡的 TA」（不帶私下那欄）；不認識的號就說不知道是誰。不認名字 | true |
| `mention_max` | 提到的人最多帶幾個 | 3 |
| `inject_heart` | 說話時帶上相關片段：心裡的 TA（稱呼、印象、梗、語氣、牽掛）說話時一定會帶（10-24 起，取代舊的每週人物卡）；這個開了再多帶一兩段跟這句話相關的片段。新功能，先看過效果再開 | false |
| `heart_moments` | 每次最多帶幾段片段：和這句話沾邊的優先，沒有沾邊的就帶最近一段 | 2 |

## 資料

執行資料在 `data/plugin_data/astrbot_plugin_lively_bond/`。

## 許可

MIT，見 [LICENSE](LICENSE)。
