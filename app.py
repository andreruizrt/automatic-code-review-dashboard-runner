import hashlib
import json
import os
import shutil
import subprocess
import time
import psycopg2


def get_db_connection():
    db_host = os.environ.get('DB_HOST')
    db_name = os.environ.get('DB_NAME')
    db_user = os.environ.get('DB_USER')
    db_port = os.environ.get('DB_PORT')
    db_password = os.environ.get('DB_PASSWORD')

    conn = psycopg2.connect(
        host=db_host,
        dbname=db_name,
        user=db_user,
        password=db_password,
        port=db_port
    )

    return conn


def __write_config(extension, path_resources, path_extensions, path_source, path_output, changes):
    path_config = path_resources + "/configs/" + extension + "/config.json"
    path_config_final = path_extensions + '/' + extension + "/config.json"

    with open(path_config, 'r') as arquivo:
        config = json.load(arquivo)
        extension_language = config['language']
        config['path_source'] = path_source
        config['path_output'] = path_output
        config['merge'] = {
            'changes': changes
        }

    with open(path_config_final, 'w') as arquivo:
        json.dump(config, arquivo, indent=True)

    return {
        "path": path_config_final,
        "language": extension_language
    }


def __comment_and_snipset(comment, path):
    comment_str = comment['comment']

    if 'position' in comment and comment['position']['snipset']:
        position = comment['position']
        path = path + "/" + position['path']

        lines = []

        with open(path, 'r') as file:
            for line in file:
                lines.append(line)

        start = position['startInLine']
        end = position['endInLine']
        type_snipset = ''

        if 'language' in position:
            type_snipset = position['language']

        snipset = ''.join(lines[start - 1:end])
        comment_str = f"""{comment_str}

```{type_snipset}
{snipset}
```
"""

    return comment_str


def __exec_extension(extension_name, extension_path, extension_language, path_config):
    if "JAVA" in extension_language:
        java_jar = f"{extension_path}/{extension_name}.jar"
        print(f'automatic-code-review::review - {extension_name} run start [APP_JAVA] {java_jar}')
        retorno = subprocess.run(["java", "-jar", java_jar, f"--CONFIG_PATH={path_config}"])

    elif "JAVASCRIPT" in extension_language:
        path_javascript_app = f"{extension_path}/app.js"
        print(f'automatic-code-review::review - {extension_name} run start [APP_JAVASCRIPT] {path_javascript_app}')
        retorno = subprocess.run(["node", path_javascript_app])

    else:
        path_python_app = f"{extension_path}/app.py"
        print(f'automatic-code-review::review - {extension_name} run start [APP_PYTHON] {path_python_app}')
        retorno = subprocess.run(['python3.10', path_python_app])

    return retorno.returncode


def __generate_md5(string):
    md5_hash = hashlib.md5()
    md5_hash.update(string.encode('utf-8'))

    return md5_hash.hexdigest()


def main():
    nr_seconds_next_attempt = int(os.environ.get('NR_SECONDS_NEXT_ATTEMPT'))

    while True:
        print("Iniciando verificação de processamento pendente...")
        conn = get_db_connection()
        cur = conn.cursor()

        cur.execute("SELECT id_execution, id_project FROM execution WHERE tp_status = 0")
        executions = cur.fetchall()

        for execution in executions:
            id_execution, id_project = execution
            print(f"Executando processamento {id_execution}")

            cur.execute(
                "UPDATE execution SET dh_started = CURRENT_TIMESTAMP, ds_detail = 'Processando...' WHERE id_execution = %s",
                (id_execution,))

            cur.execute("DELETE FROM issue WHERE id_project = %s", (id_project,))

            cur.execute(
                'SELECT P.lk_repository, P.ds_branch_name, G.ds_name FROM project P JOIN "group" G ON G.id_group = P.id_group WHERE P.id_project = %s',
                (id_project,))
            project_url, branch_name, group_name = cur.fetchone()

            code_path = f"/tmp/code/{group_name}"
            if os.path.isdir(code_path):
                shutil.rmtree(code_path)
            os.makedirs(code_path)
            command = ["git", "clone", "-b", branch_name, project_url, code_path]
            subprocess.run(command)

            path_resources = f"groups/{group_name}/resources"
            path_extensions = f"{path_resources}/extensions"

            comments = []
            qt_comments_total = 0

            changes = []
            for root, dirs, files in os.walk(code_path):
                for file in files:
                    file_path = os.path.join(root, file)
                    changes.append({
                        'new_path': file_path.replace(code_path, "")[1:]
                    })

            for extension_name in os.listdir(path_extensions):
                extension_path = os.path.join(path_extensions, extension_name)

                if os.path.isdir(extension_path):
                    path_output = path_resources + "/output/" + extension_name + "_output.json"

                    config = __write_config(
                        extension=extension_name,
                        path_resources=path_resources,
                        path_extensions=path_extensions,
                        path_source=code_path,
                        path_output=path_output,
                        changes=changes,
                    )

                    path_extension = path_extensions + "/" + extension_name
                    retorno = __exec_extension(extension_name, path_extension, config["language"], config["path"])

                    if retorno != 0:
                        print(f'automatic-code-review::review - {extension_name} fail')
                        comment_id = __generate_md5(f"automatic-code-review::review::{extension_name}::fail")
                        comments.append({
                            'id': f"{extension_name}:{comment_id}",
                            'comment': f"Failed to run {extension_name} extension, contact administrator",
                            'type': extension_name
                        })
                        continue

                    print(f'automatic-code-review::review - {extension_name} run end, start read output')

                    with open(path_output, 'r') as arquivo:
                        comments_by_extension = json.load(arquivo)
                        qt_comments = len(comments_by_extension)
                        qt_comments_total += qt_comments

                        print(f'automatic-code-review::review - {extension_name} [QT_COMMENTS] {qt_comments}')

                        for comment in comments_by_extension:
                            comment_id = comment['id']

                            comment['type'] = extension_name
                            comment['id'] = f"{extension_name}:{comment_id}"
                            comment['comment'] = __comment_and_snipset(comment, code_path)

                            comments.append(comment)

            # TODO TRATAR QUE QUALQUER FALHA DA UM ROOLBACK E GRAVA NO DETALHE A FALHA
            # TODO DISPARAR ESSA THREAD QUANDO CHAMAR A EXECUCAO, NO CASO CRIAR UM ENDPOINT E ESSE ENDPOINT PARAR O THREAD SLEEP
            # TODO SE DER FALHA AGENDAR PARA DAQUI A X SEGUNDOS E DAI FICAR PROCESSANDO ATE NAO TER MAIS NADA

            for comment in comments:
                position = comment['position']
                tx_issue = comment['comment']
                lk_file = position['path']
                nr_start_line = position['startInLine']
                nr_end_line = position['endInLine']
                tp_issue = comment['type']

                cur.execute("""
                    INSERT INTO issue (
                        tx_issue,
                        lk_file,
                        nr_start_line,
                        nr_end_line,
                        tp_issue,
                        id_project
                    ) VALUES (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )
                """, (
                    tx_issue,
                    lk_file,
                    nr_start_line,
                    nr_end_line,
                    tp_issue,
                    id_project
                ))

            cur.execute(
                "UPDATE execution SET qt_issue = %s, tp_status = 2, dh_ended = CURRENT_TIMESTAMP, ds_detail = 'Processamento finalizado com sucesso' WHERE id_execution = %s",
                (qt_comments_total, id_execution,))
            conn.commit()

            print(f"Processamento {id_execution} finalizado")

        cur.close()
        conn.close()

        print("Aguardando proxima tentativa")
        time.sleep(nr_seconds_next_attempt)


if __name__ == '__main__':
    main()
