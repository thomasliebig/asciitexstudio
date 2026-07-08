<script setup lang="ts">
import loader from '@monaco-editor/loader'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps<{ modelValue: string; language: string }>()
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
          [/\\(?:section|subsection|subsubsection|title|header|footer|label|ref|cite|bibentry|bibitem|bibliography|bibliographystyle|includeimage|begindiagram|enddiagram|item|columnbreak|floatbarrier|hr|quote|verbatim|underbrace)\b/, 'keyword'],
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
  editor = monaco.editor.create(host.value!, {
    value: props.modelValue,
    language: props.language,
    theme: 'vs-dark',
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
