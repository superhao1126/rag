import { message } from 'ant-design-vue'

export function useToast() {
  function show(msg: string, t: 'success' | 'error' | 'info' = 'info') {
    message[t](msg)
  }

  return { show }
}
