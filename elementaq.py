for el in elements:
            # 1. Валидные CCV (без !!)
            ccv_data = ph1_df[(ph1_df['Type'] == 'CCV') & (~ph1_df[el].astype(str).str.contains('!!'))]
            
            if ccv_data.empty:
                continue

            target_v = ccv_data['Target'].iloc[0]
            ccv_map = {idx: float(re.sub(r'[^0-9.eE-]', '', str(v).split('!')[0])) 
                       for idx, v in zip(ccv_data['Row_Idx'], ccv_data[el])}

            # 2. Сначала корректируем все бланки по дрейфу
            corrected_blanks = []
            blk_rows = ph1_df[ph1_df['Type'] == 'BLK']
            for idx, row in blk_rows.iterrows():
                raw_blk_val = float(re.sub(r'[^0-9.eE-]', '', str(row[el]).split('!')[0]))
                f_drift_blk = calculate_drift_factor(idx, ccv_map, target_v)
                corrected_blanks.append(raw_blk_val * f_drift_blk)
            
            avg_blank_corrected = np.mean(corrected_blanks) if corrected_blanks else 0.0

            # 3. Итоговый расчет для образцов
            for i, row in ph2_df.iterrows():
                raw_val = float(re.sub(r'[^0-9.eE-]', '', str(row[el]).split('!')[0]))
                is_lq = '<' in str(row[el])
                
                if row['Type'] == 'S':
                    f_drift_s = calculate_drift_factor(i, ccv_map, target_v)
                    # Вычитаем поправленный фон из поправленного образца
                    final_val = (raw_val * f_drift_s - avg_blank_corrected) * row['Dilution']
                    ph2_df.at[i, el] = format_value(final_val, is_lq)
                else:
                    # Оставляем как есть для контроля в таблице
                    ph2_df.at[i, el] = row[el]
