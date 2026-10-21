import { redirect } from 'next/navigation';

/**
 * 首页：直接进入对话主页。
 *
 * 说明：产品以「开箱即用」为原则，登录由身份服务托管页承担，
 * 前端不实现任何登录表单（架构文档 3.0.2）。
 *
 * @returns 不返回，直接重定向
 */
export default function IndexPage(): never {
  redirect('/chat');
}
