# 好感度與印象卡

> 好感度系統：熟悉度、好感、心情、印象、每週人物卡，群友要「印象卡」時發一張印象卡圖片

AstrBot 插件，屬於「擬真群友」（lively）插件套件。適用 AstrBot >=4.29.0 + NapCat（aiocqhttp）。

主人、群、機器人名字、人設用詞這些共用設定在 [astrbot_plugin_lively_gate](https://github.com/GUCine/astrbot_plugin_lively_gate) 裡填，建議一起裝。

## 功能

```text
機器人的好感度：熟悉度、好感、心情、印象、人物卡、好久不見，以及機器人自己此刻的狀態。

人物卡：每週（CARD_EVERY）從印象變化、記憶和 TA 對機器人說過的話裡，給常聊的人整理一張小卡（稱呼、喜好、雷區、梗、最近在忙），
TA 說話時和關係狀態一起告訴模型
```

## 依賴

可選 [LivingMemory](https://github.com/lxfight/astrbot_plugin_livingmemory)：做人物卡時會讀它的記憶和聊天記錄。

## 素材

卡片的手寫字體不隨倉庫發佈：從 [PC2005-cloud/dsh-pet](https://github.com/PC2005-cloud/dsh-pet) 取得後放到 `card_assets/rt.woff2`；沒放就用中文黑體（Debian/Ubuntu 裝 `fonts-noto-cjk`）。`card_assets/fish.webp` 是預設角色的形象圖，可以換成自己的。

## 配置

在 AstrBot WebUI 的插件配置裡填，或者直接編輯 `data/config/astrbot_plugin_lively_bond_config.json`。留空的公共項（主人、群、機器人名字、平台）會用 astrbot_plugin_lively_gate 裡填的。

| 配置項 | 說明 | 預設 |
| --- | --- | --- |
| `provider_id` | 做人物卡用的模型：每週給常聊的人整理人物卡時用。留空用 AstrBot 預設模型 | （空） |
| `groups` | 記錄好感的群：留空就用門禁插件裡填的群 | （空） |
| `owner_qq` | 主人 QQ：主人好感和熟悉度固定是滿的，也只有主人能用 /好感 指令。留空用門禁插件裡填的 | （空） |
| `card_auto` | 群友要印象卡時自動發：有人叫機器人、而且是在問它對自己的印象時，直接發一張印象卡圖片（語義判斷，不用關鍵詞） | true |
| `embedding_provider_id` | 判斷用的向量模型：填向量模型的 ID，留空用第一個向量模型（比如 LivingMemory 用的那個） | （空） |
| `card_examples` | 算在要印象卡的例句：起步例句，意思像這些就算；機器人還會自己學新說法 | ["你对我的印象怎么样", "给我看看我的印象卡", "我在你心里是什么样…" |
| `card_not_examples` | 不算要印象卡的例句：話題像但不是要卡的（問別人、誇機器人、別的卡），用來排除 | ["你对他的印象怎么样", "你觉得她这个人怎么样", "我对你的印象很好…" |
| `card_min_sim` | 至少要多像：和要卡例句的相似度門檻（0～1），調高更嚴 | 0.7 |
| `card_margin` | 比反例高多少才算：和要卡例句的相似度要比和反例的高出這麼多 | 0.05 |
| `card_learn` | 自己學新說法：模型自己判斷出有人在要卡、而例句沒認出來時，把那句話記成新例句 | true |

## 資料

執行資料在 `data/plugin_data/astrbot_plugin_lively_bond/`。

## 許可

MIT，見 [LICENSE](LICENSE)。
