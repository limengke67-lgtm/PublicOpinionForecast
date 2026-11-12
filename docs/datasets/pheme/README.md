# PHEME 匿名小时计数

由原始 PHEME rumours and non-rumours 归档派生。数据发布者：Arkaitz Zubiaga、Geraldine Wong Sak Hoi、Maria Liakata、Rob Procter。

来源：[Figshare / DOI 10.6084/m9.figshare.4010619.v1](https://doi.org/10.6084/m9.figshare.4010619.v1)。Figshare 元数据标注许可为 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)。原始帖子的权利归相应权利人；本目录仅包含派生计数，不包含帖子正文、用户信息或单条帖子 ID。

转换过程：源帖和回复按事件及帖子 ID 去重，以最早源帖所在整点为开始，聚合前 72 小时；每行时间表示 UTC 小时桶结束时刻。空桶只代表固定公开对话样本中没有记录，不代表全 Twitter 没有讨论。

没有使用谣言真假标签作为情感标签，没有将样本量放大为全平台总量。来源、归档哈希、去重数量及时间窗见 [source.json](./source.json)。转换脚本为 `backend/scripts/prepare_pheme.py`。

该数据适合演示与复现数量管道、历史回放和数值校准，不能单独验证当代中文舆情预测、多智能体语义推演能力或前瞻效果。
