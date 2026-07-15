<script setup lang="ts">
import loader from '@monaco-editor/loader'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{ modelValue: string; language: string; themeMode: 'green' | 'amber' }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; change: []; 'line-dblclick': [line: number] }>()
const host = ref<HTMLElement>()
const currentLine = ref(1)
let editor: any
let monaco: any
let applyingExternalValue = false
let resizeObserver: ResizeObserver | undefined
let layoutFrame = 0

loader.config({ paths: { vs: 'https://cdn.jsdelivr.net/npm/monaco-editor@0.52.2/min/vs' } })

onMounted(async () => {
  monaco = await loader.init()
  if (!monaco.languages.getLanguages().some((entry: any) => entry.id === 'asciitex')) {
    monaco.languages.register({ id: 'asciitex', extensions: ['.tex'], aliases: ['AsciiTeX'] })
    monaco.languages.setMonarchTokensProvider('asciitex', {
      defaultToken: '',
      tokenizer: {
        root: [
          [/%.*/, 'comment'],
          [/\\(?:begin|end)\s*\{[^}]+\}/, 'keyword'],
          [/\\(?:section|subsection|subsubsection|title|header|footer|label|ref|cite|bibentry|bibitem|bibliography|bibliographystyle|input|include|includeimage|begindiagram|enddiagram|item|columnbreak|floatbarrier|hr|quote|verbatim|underbrace|textbf|textit|textbfit|emph)\b/, 'keyword'],
          [/\\[A-Za-z]+/, 'type.identifier'],
          [/\[[^\]]*\]/, 'attribute.value'],
          [/\{/, { token: 'delimiter.curly', next: '@braces' }],
          [/\b(?:true|false|on|off|yes|no)\b/i, 'constant'],
          [/-?\d+(?:\.\d+)?/, 'number'],
        ],
        braces: [
          [/[^{}\\]+/, 'string'],
          [/\\[A-Za-z]+/, 'type.identifier'],
          [/\{/, 'delimiter.curly', '@push'],
          [/\}/, 'delimiter.curly', '@pop'],
        ],
      },
    })
    monaco.languages.setLanguageConfiguration('asciitex', {
      comments: { lineComment: '%' },
      brackets: [['{', '}'], ['[', ']']],
      autoClosingPairs: [{ open: '{', close: '}' }, { open: '[', close: ']' }],
      surroundingPairs: [{ open: '{', close: '}' }, { open: '[', close: ']' }],
    })
  }
  monaco.editor.defineTheme('asciitex-retro-green', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: '', foreground: 'd8f4dc' },
      { token: 'comment', foreground: '628f6d', fontStyle: 'italic' },
      { token: 'keyword', foreground: '8ff6a6', fontStyle: 'bold' },
      { token: 'type.identifier', foreground: 'b9f7c5' },
      { token: 'attribute.value', foreground: 'd6e98a' },
      { token: 'string', foreground: 'c4f7cf' },
      { token: 'constant', foreground: 'f0d37a' },
      { token: 'number', foreground: 'a4d7ff' },
      { token: 'delimiter.curly', foreground: '7ed992' },
    ],
    colors: {
      'editor.background': '#07120b',
      'editor.foreground': '#d8f4dc',
      'editorLineNumber.foreground': '#416349',
      'editorLineNumber.activeForeground': '#8ff6a6',
      'editorCursor.foreground': '#a9ffb9',
      'editor.selectionBackground': '#245534',
      'editor.inactiveSelectionBackground': '#1a3524',
      'editor.lineHighlightBackground': '#102518',
      'editor.lineHighlightBorder': '#21492d',
      'editorGutter.background': '#08130c',
      'editorIndentGuide.background1': '#193420',
      'editorIndentGuide.activeBackground1': '#4f8c5f',
      'editorWidget.background': '#102018',
      'editorWidget.border': '#3d6248',
      'editorSuggestWidget.background': '#102018',
      'editorSuggestWidget.border': '#3d6248',
      'editorSuggestWidget.selectedBackground': '#1f432b',
    },
  })
  monaco.editor.defineTheme('asciitex-retro-amber', {
    base: 'vs-dark',
    inherit: true,
    rules: [
      { token: '', foreground: 'f6dfac' },
      { token: 'comment', foreground: '9f7d45', fontStyle: 'italic' },
      { token: 'keyword', foreground: 'ffd36d', fontStyle: 'bold' },
      { token: 'type.identifier', foreground: 'ffe2a0' },
      { token: 'attribute.value', foreground: 'bfe08a' },
      { token: 'string', foreground: 'f5c981' },
      { token: 'constant', foreground: 'ffeb9a' },
      { token: 'number', foreground: 'ffc266' },
      { token: 'delimiter.curly', foreground: 'd89f45' },
    ],
    colors: {
      'editor.background': '#130d05',
      'editor.foreground': '#f6dfac',
      'editorLineNumber.foreground': '#72562d',
      'editorLineNumber.activeForeground': '#ffd36d',
      'editorCursor.foreground': '#ffd36d',
      'editor.selectionBackground': '#5a3514',
      'editor.inactiveSelectionBackground': '#35220f',
      'editor.lineHighlightBackground': '#241606',
      'editor.lineHighlightBorder': '#5b3512',
      'editorGutter.background': '#120c05',
      'editorIndentGuide.background1': '#37220c',
      'editorIndentGuide.activeBackground1': '#a36c24',
      'editorWidget.background': '#201406',
      'editorWidget.border': '#7a501c',
      'editorSuggestWidget.background': '#201406',
      'editorSuggestWidget.border': '#7a501c',
      'editorSuggestWidget.selectedBackground': '#4a2d0d',
    },
  })
  editor = monaco.editor.create(host.value!, {
    value: props.modelValue,
    language: props.language,
    theme: props.themeMode === 'amber' ? 'asciitex-retro-amber' : 'asciitex-retro-green',
    fontFamily: 'JetBrains Mono, Cascadia Code, Consolas, monospace',
    fontSize: 14,
    lineHeight: 23,
    minimap: { enabled: false },
    wordWrap: 'on',
    padding: { top: 18 },
    scrollBeyondLastLine: false,
    automaticLayout: false,
    renderLineHighlight: 'gutter',
  })
  editor.onDidChangeModelContent(() => {
    if (applyingExternalValue) return
    emit('update:modelValue', editor.getValue())
    emit('change')
  })
  editor.onMouseDown((event: any) => {
    if (event.event.detail === 2 && event.target.position?.lineNumber) {
      emit('line-dblclick', event.target.position.lineNumber)
    }
  })
  editor.onDidChangeCursorPosition((event: any) => { currentLine.value = event.position.lineNumber })
  resizeObserver = new ResizeObserver(entries => {
    const bounds = entries[0]?.contentRect
    if (!bounds || bounds.width <= 0 || bounds.height <= 0) return
    cancelAnimationFrame(layoutFrame)
    layoutFrame = requestAnimationFrame(() => editor?.layout({ width: bounds.width, height: bounds.height }))
  })
  resizeObserver.observe(host.value!)
})

watch(() => props.modelValue, value => {
  if (!editor || editor.getValue() === value) return
  applyingExternalValue = true
  editor.setValue(value)
  applyingExternalValue = false
})

watch(() => props.language, language => {
  if (editor && monaco) monaco.editor.setModelLanguage(editor.getModel(), language)
})

watch(() => props.themeMode, themeMode => {
  if (editor && monaco) monaco.editor.setTheme(themeMode === 'amber' ? 'asciitex-retro-amber' : 'asciitex-retro-green')
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  cancelAnimationFrame(layoutFrame)
  editor?.dispose()
})

function goToLine(line: number): void {
  if (!editor) return
  editor.setPosition({ lineNumber: Math.max(1, line), column: 1 })
  editor.revealLineInCenter(Math.max(1, line))
  editor.focus()
}

defineExpose({ goToLine })
</script>

<template><div ref="host" class="monaco-host" :data-cursor-line="currentLine" /></template>

<style scoped>
.monaco-host { width: 100%; max-width: 100%; height: 100%; min-width: 0; overflow: hidden; contain: strict; }
</style>
