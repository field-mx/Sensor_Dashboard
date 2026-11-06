import argparse
import uvicorn

def main():
    parser=argparse.ArgumentParser(description='仅本机访问的电解槽Demo后台')
    parser.add_argument('--port',type=int,default=18000)
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('端口需为1—65535')
    from .app import app
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=args.port,workers=1,access_log=False))
    app.state.request_shutdown=lambda:setattr(server,'should_exit',True)
    server.run()
    if not server.started:
        print('后台未启动；同一数据目录只允许一个后台。请查看启动日志，检查端口和数据目录。')
        return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
