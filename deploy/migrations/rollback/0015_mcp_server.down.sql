-- ===========================================================================
-- 0015 回滚：删除 MCP server 登记表
--
-- ⚠ **会丢失全部外部服务登记**（地址、白名单、凭据引用）。凭据密文本身在
--   `credential_store`，不会被本回滚删除；但引用丢失后需要重新登记。
--
-- 顺序：删策略 → 删索引 → 删表；全部 IF EXISTS，重复执行零副作用。
-- ===========================================================================

DROP POLICY IF EXISTS p_tenant_isolation ON mcp_server;
DROP INDEX IF EXISTS idx_mcp_server_enabled;
DROP TABLE IF EXISTS mcp_server;

DO $down$
BEGIN
    RAISE NOTICE '0015: 已回滚（外部服务登记已删除）';
END $down$;
