import * as BrowserFS from 'browserfs'

export type ProjectFile = {
  path: string
  data: Uint8Array
  text: boolean
}

const TEXT_EXTENSIONS = new Set([
  'tex', 'bib', 'txt', 'md', 'json', 'csv', 'py', 'yaml', 'yml', 'toml', 'svg',
])

const seedFiles: Record<string, string> = {
  '/main.tex': String.raw`\title{AsciiTeX Studio}
\header{LIVE DOCUMENT}

\section{Welcome}
This document is compiled entirely in your browser. Edit the source, add files,
and watch the Unicode preview update. See Equation \ref{eq:pythagoras} and \cite{knuth1981}.

\label{eq:pythagoras}
\begin{equation}
  a^2 + b^2 = c^2
\end{equation}

\begin{itemize}
\item Monaco edits the project files.
\item BrowserFS stores them locally.
\item Pyodide runs the original Python compiler.
\end{itemize}

\section{Image}
The image below is loaded from the project file system and converted to Unicode art.

\label{fig:asciitex}
\includeimage[width=52,caption="AsciiTeX sample image",frame=true]{image.png}

\section{Diagram}
This plot is generated directly by the AsciiTeX diagram extension.

\label{dia:pipeline}
\begindiagram[width=72,height=18,mode=spec,caption="Compilation pipeline",frame=true]
{
  "type": "lines",
  "title": "Compilation pipeline",
  "x_label": "stage",
  "y_label": "progress",
  "grid": True,
  "legend": True,
  "lines": [{"x": [0, 1, 2, 3, 4], "y": [0.2, 1.4, 2.7, 3.4, 4.6], "name": "pipeline", "ch": "•"}]
}
\enddiagram

\section{Two-column layout}
\begin{twocolumns}[textwidth=\textwidth,gutter=4,balance=true]
\subsection{Why text columns?}
Two columns make compact technical notes easier to scan. Content stays in reading
order and flows from the left column into the right column.

\begin{itemize}
\item readable source
\item compact output
\item Unicode-native layout
\end{itemize}

\subsection{Project workflow}
Keep the TeX source, bibliography and image assets together in the file browser.
BrowserFS persists every file locally while Pyodide compiles the complete project.
\end{twocolumns}

\bibliography{refs.bib}
\footer{Rendered with AsciiTeX}`,
  '/refs.bib': `@article{knuth1981,
  author = {Donald E. Knuth and Michael F. Plass},
  title = {Breaking Paragraphs into Lines},
  journal = {Software: Practice and Experience},
  volume = {11},
  pages = {1119--1184},
  year = {1981}
}\n`,
  '/README.txt': `AsciiTeX Studio project\n\nOpen main.tex to edit the document. Files are stored in your browser.\n`,
}

seedFiles['/main.tex'] = String.raw`% !asciitex example-version=7
% !asciitex hyphenation=hyph-en-us.pat.txt
% !asciitex German: change the line above to hyphenation=hyph-de-1996.pat.txt

\title{AsciiTeX Studio}
\header{LIVE DOCUMENT}

\section{Welcome}
This document is compiled entirely in your browser. English Liang hyphenation patterns
are loaded automatically. Cross-references may point forward or backward: Equations
\ref{eq:showcase}--\ref{eq:cases}, Figure \ref{fig:asciitex}, Table \ref{tab:features},
Boxes \ref{box:single}--\ref{box:double}, Diagram \ref{dia:pipeline}, and \cite{knuth1981}.

\label{eq:showcase}
\begin{equation}
  \sum_{i=1}^{n} \frac{\sqrt{x_i^2 + \alpha}}{1 + x_i}
  = \left( \begin{bmatrix} a & b \\ c & d \end{bmatrix} \right)_{k}^{2}
\end{equation}

\subsection{Underbraces with labels}
Use an underbrace to name a meaningful part or cite another numbered equation.

\label{eq:underbrace}
\begin{equation}
  S_n = \underbrace{a_1 + a_2 + \cdots + a_n}_{n terms}
  + \underbrace{r_1 + r_2}_{from Eq. \ref{eq:showcase}}
\end{equation}
Equation \ref{eq:underbrace} embeds a reference to Equation \ref{eq:showcase}
inside its own underbrace label.

\subsection{Aligned equation systems}
The eqnarray environment aligns corresponding columns across several equations.

\label{eq:system}
\begin{eqnarray}
  2x + y & = & 7 \\
  -x + 3y & = & 5 \\
  x - y & = & 1
\end{eqnarray}

\subsection{Cases with a vertical brace}
The cases environment groups alternatives below one vertically stretched brace. Compare
Equation \ref{eq:system} with the piecewise definition below.

\label{eq:cases}
\begin{equation}
  f(x) = \begin{cases}
    -1 & x < 0 \\
    0 & x = 0 \\
    1 & x > 0
  \end{cases}
\end{equation}

\begin{itemize}
\item Monaco edits the project files.
\item BrowserFS stores them locally.
\item Pyodide runs the original Python compiler.
\end{itemize}

\section{Image using textwidth}
The image uses a scaled \textwidth dimension and is converted to Unicode art.

\label{fig:asciitex}
\includeimage[width=.72\textwidth,caption="AsciiTeX sample image",frame=true]{image.png}
Figure \ref{fig:asciitex} is referenced by its independent figure counter.

\section{Table using textwidth}
Table \ref{tab:features} summarizes the examples and has its own counter.
\label{tab:features}
\begin{asciitable}[width=\textwidth,align=lcr,header=true,frame=double,caption="AsciiTeX feature matrix"]
Feature & Syntax & Scope
Math & equation & block
Image & includeimage & textwidth
Diagram & begindiagram & columnwidth
Table & asciitable & textwidth
\end{asciitable}

\section{Decorated boxes}
Boxes use a separate counter: Box \ref{box:single}, Box \ref{box:rounded}, and Box \ref{box:double}.
\label{box:single}
\begin{box}[width=\textwidth,style=single,title="Single"]
A single-line box is useful for neutral notes and short explanations.
\end{box}

\label{box:rounded}
\begin{box}[width=\textwidth,style=rounded,title="Rounded"]
Rounded corners work well for friendly hints and examples.
\end{box}

\label{box:double}
\begin{box}[width=\textwidth,style=double,title="Double"]
Double borders provide stronger visual emphasis for important results.
\end{box}

\section{Horizontal rules}
Single rule:
\hr[style=single,width=\textwidth]
Double rule:
\hr[style=double,width=\textwidth]
Heavy rule:
\hr[style=heavy,width=\textwidth]
Dashed rule:
\hr[style=dashed,width=\textwidth]
Dotted rule:
\hr[style=dotted,width=\textwidth]

\section{Two-column layout}
\begin{twocolumns}[textwidth=\textwidth,gutter=4,balance=true]
\subsection{Why text columns?}
Two columns make compact technical notes easier to scan. Content stays in reading
order and flows from the left column into the right column.

\begin{itemize}
\item readable source
\item compact output
\item Unicode-native layout
\end{itemize}

\subsection{Project workflow}
Keep the TeX source, bibliography and image assets together in the file browser.
BrowserFS persists every file locally while Pyodide compiles the complete project.

\subsection{Column-width diagram}
The plot below uses the active \columnwidth instead of the full text width.

\label{dia:pipeline}
\begindiagram[width=\columnwidth,height=15,mode=spec,caption="Compilation pipeline",frame=true]
{
  "type": "lines",
  "title": "Pipeline",
  "x_label": "stage",
  "y_label": "progress",
  "grid": True,
  "legend": False,
  "lines": [{"x": [0, 1, 2, 3, 4], "y": [0.2, 1.4, 2.7, 3.4, 4.6], "name": "pipeline", "ch": "•"}]
}
\enddiagram
Diagram \ref{dia:pipeline} remains referenceable from either column and from full-width text.
\end{twocolumns}

\bibliography{refs.bib}
\footer{Rendered with AsciiTeX}`

let fs: any

function call<T>(fn: (...args: any[]) => void, ...args: any[]): Promise<T> {
  return new Promise((resolve, reject) => {
    fn(...args, (error: Error | null, value: T) => error ? reject(error) : resolve(value))
  })
}

export async function initProjectFs(): Promise<void> {
  await new Promise<void>((resolve, reject) => {
    BrowserFS.configure({ fs: 'IndexedDB', options: { storeName: 'asciitex-studio' } }, (error: Error | null) => {
      if (error) reject(error)
      else resolve()
    })
  })
  fs = BrowserFS.BFSRequire('fs')
  const names = await call<string[]>(fs.readdir.bind(fs), '/')
  if (names.length === 0) {
    for (const [path, content] of Object.entries(seedFiles)) await writeText(path, content)
  } else if (names.includes('main.tex')) {
    let currentMain = await readText('/main.tex')
    if (currentMain.includes('AsciiTeX sample image') && !currentMain.includes('example-version=7')) {
      await writeText('/main.tex', seedFiles['/main.tex'])
      currentMain = seedFiles['/main.tex']
    } else if (currentMain.includes('Monaco edits the project files.') && !currentMain.includes('\\includeimage')) {
      await writeText('/main.tex', seedFiles['/main.tex'])
    } else if (currentMain.includes('AsciiTeX sample image') && currentMain.includes('.55\\textwidth')) {
      currentMain = currentMain
        .replace('.55\\textwidth', '52')
        .replace('.72\\textwidth', '72')
      await writeText('/main.tex', currentMain)
    }
    if (currentMain.includes('AsciiTeX sample image') && !currentMain.includes('\\begin{twocolumns}')) {
      const twoColumnExample = String.raw`
\section{Two-column layout}
\begin{twocolumns}[textwidth=\textwidth,gutter=4,balance=true]
\subsection{Why text columns?}
Two columns make compact technical notes easier to scan. Content stays in reading
order and flows from the left column into the right column.

\begin{itemize}
\item readable source
\item compact output
\item Unicode-native layout
\end{itemize}

\subsection{Project workflow}
Keep the TeX source, bibliography and image assets together in the file browser.
BrowserFS persists every file locally while Pyodide compiles the complete project.
\end{twocolumns}

`
      const migratedMain = (await readText('/main.tex')).replace('\\bibliography{refs.bib}', `${twoColumnExample}\\bibliography{refs.bib}`)
      await writeText('/main.tex', migratedMain)
    }
  }
  const updatedNames = await call<string[]>(fs.readdir.bind(fs), '/')
  let needsExampleImage = !updatedNames.includes('image.png')
  if (!needsExampleImage) {
    const existing = new Uint8Array(await call<any>(fs.readFile.bind(fs), '/image.png'))
    needsExampleImage = existing.length < 8 || existing[0] !== 0x89 || existing[1] !== 0x50 || existing[2] !== 0x4e || existing[3] !== 0x47
  }
  if (needsExampleImage) {
    const response = await fetch(`${import.meta.env.BASE_URL}examples/image.png`)
    if (response.ok) await writeBinary('/image.png', new Uint8Array(await response.arrayBuffer()))
  }
}

export function isTextPath(path: string): boolean {
  const ext = path.split('.').pop()?.toLowerCase() ?? ''
  return TEXT_EXTENSIONS.has(ext)
}

export async function listFiles(): Promise<ProjectFile[]> {
  const names = (await call<string[]>(fs.readdir.bind(fs), '/')).sort((a, b) => a.localeCompare(b))
  return Promise.all(names.map(async name => {
    const path = `/${name}`
    const buffer = await call<any>(fs.readFile.bind(fs), path)
    return { path, data: new Uint8Array(buffer), text: isTextPath(path) }
  }))
}

export async function readText(path: string): Promise<string> {
  const data = await call<any>(fs.readFile.bind(fs), path, 'utf8')
  return String(data)
}

export async function writeText(path: string, content: string): Promise<void> {
  await call<void>(fs.writeFile.bind(fs), path, content, 'utf8')
}

export async function writeBinary(path: string, content: Uint8Array): Promise<void> {
  const Buffer = BrowserFS.BFSRequire('buffer').Buffer
  await call<void>(fs.writeFile.bind(fs), path, Buffer.from(content))
}

export async function removeFile(path: string): Promise<void> {
  await call<void>(fs.unlink.bind(fs), path)
}

export async function renameFile(from: string, to: string): Promise<void> {
  await call<void>(fs.rename.bind(fs), from, to)
}
