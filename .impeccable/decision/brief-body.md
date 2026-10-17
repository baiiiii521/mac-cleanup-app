## Scope and mode

表面：`app/index.html`（清理助手的唯一界面，由原生 App 窗口内的 WKWebView 承载，同一页也能在浏览器/手机打开）。
访客模式：**Operate** —— 用户来把一件事办完：看清磁盘现状 → 逐项勾选 → 预览 → 清理 → 收尾。
code-led 构建（Operate 表面按契约即 code-led）。

## Audience, job, action, proof, constraints

主用户 Barry 偶尔打开一次；次用户是偶尔借用他电脑的同事/家人，第一次看必须几秒内看懂。
要做的事：分清「能删 / 要判断 / 别动」，然后安全地释放空间。
内容证据：本机真实体检数据（228.3 GB 中可用 ~47 GB；38 项；可勾选 8 项 4.80 GB；
需判断 22.03 GB；规则表认不出 4 项带 Jev 判断，如 `~/Library` 可删概率 0.03 → 别删）。
必须保留：全部现有功能与全部安全锁（默认预览、执行需「确认清理」、勾选只做交集、清空废纸篓二次确认）、
引擎接口不变、单文件零依赖离线可用、键盘可达与 `prefers-reduced-motion`。

## Chosen direction and memorable moment

**档案抽屉 · 除尘与退役**（骰判）。世界学自图书馆卡片目录与馆藏退役流程，只借四样：字体、调色、
密度、一个签名动作——布局、导航、控件仍是本平台的标准件。
签名动作：抽屉带着重量滑出，选中的卡被抽出半个身位；执行清理时卡片升起、散成灯下尘埃流向磁盘环，
可用空间数字随之跳格；刚变化的那一行留在原处发亮，直到你真的看见。

## Unresolved decisions

界面语言：中文优先（已定）。桌面端主设计宽度 1440；手机端降级（粒子减量）。

## Direction contract

THESIS：把「清理」当一次馆藏退役来办——每条记录都有编号、体量刻度和处置意见；拒绝这一类工具默认的
「顶部大数字 + 彩色进度条 + 卡片网格」。
OWN-WORLD：暖墨地（#120F0C）＋上蜡卡纸的象牙白（#F2E4CC）＋台灯琥珀（#E8A33D）＋一处只表示
「系统管控」的冷青（#7FD1C4）＋一处只表示破坏性的朱砂（#D2603A）；字体只用系统字族的粗细与字距分级，
数值一律等宽；控件是上蜡卡纸的浅浮雕，不是玻璃与霓虹。
STORY：先看懂磁盘水位与三层抽屉的分法 → 勾选要退役的记录 → 看清预览的证据 → 执行并看到空间真的回来了。
FIRST VIEWPORT：左上是**磁盘环**——一条由实时数据重绘的白色脉冲线堆＋可用空间数字；右侧是三格抽屉
（可安全清理／需你判断／规则表认不出），每行＝编号＋名称＋等宽体量刻度＋处置意见＋复选框；
底部一条尘埃粒子带随光标流动，边缘留白厚。主操作「开始体检」在第一屏内。
FORM：档案抽屉世界，位于我自列七向中的第 7 位；SEED REFERENCE seed:0536c52950b8eb92。
FINISH：unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance.
